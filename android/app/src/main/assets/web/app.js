(function () {
  "use strict";

  var state = {
    status: null,
    profiles: [],
    activeProfile: null,
    currentView: "profiles",
    loadToken: 0,
    detailsMedia: null,
    player: null,
    progressTimer: null,
    toastTimer: null,
    manageProfiles: false,
    profilesReturnHome: false,
    detailsReturnFocus: null,
    settingsReturnFocus: null,
    textEntry: null,
    gamepadPrevious: {},
    gamepadCooldown: 0,
    gamepadFrame: 0
  };

  var ui = {};
  var nativeRequests = {};
  var nextNativeRequest = 0;
  var FIRE_TV = Boolean(window.__PISTICK_FIRE_TV__);

  function nativeUi(method) {
    if (!window.PiStickAndroid || typeof window.PiStickAndroid[method] !== "function") return;
    try {
      window.PiStickAndroid[method](String(window.__PISTICK_ANDROID_SECRET__ || ""));
    } catch (error) { /* The native UI helper is best effort. */ }
  }

  function nativePlayerKey(action) {
    if (!window.PiStickAndroid || typeof window.PiStickAndroid.sendPlayerKey !== "function") return false;
    try {
      window.PiStickAndroid.sendPlayerKey(
        String(window.__PISTICK_ANDROID_SECRET__ || ""),
        String(action || "")
      );
      return true;
    } catch (error) {
      return false;
    }
  }

  function decodeBase64JSON(encoded) {
    var bytes = Uint8Array.from(window.atob(encoded), function (character) {
      return character.charCodeAt(0);
    });
    return JSON.parse(new TextDecoder("utf-8").decode(bytes));
  }

  window.PiStickNative = {
    receive: function (requestId, succeeded, encodedPayload) {
      var pending = nativeRequests[String(requestId)];
      if (!pending) return;
      delete nativeRequests[String(requestId)];
      window.clearTimeout(pending.timeout);
      try {
        var payload = decodeBase64JSON(encodedPayload);
        if (succeeded) pending.resolve(payload);
        else pending.reject(new Error(payload.error || "The app request failed."));
      } catch (error) {
        pending.reject(error);
      }
    }
  };

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function button(text, className, onClick) {
    var node = element("button", className, text);
    node.type = "button";
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }

  function titleOf(media) {
    return String(media.title || media.name || "Untitled");
  }

  function imageUrl(path, size) {
    var value = String(path || "");
    if (!/^\/[A-Za-z0-9._/-]+$/.test(value)) return "";
    return "https://image.tmdb.org/t/p/" + size + value;
  }

  function mediaMeta(media) {
    var pieces = [];
    if (media.year) pieces.push(String(media.year));
    if (media.media_type === "tv") pieces.push("TV Show");
    if (media.media_type === "movie") pieces.push("Movie");
    if (Number(media.vote_average) > 0) pieces.push(Number(media.vote_average).toFixed(1) + " ★");
    return pieces.join(" · ");
  }

  function query(path, params) {
    var search = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      if (params[key] !== undefined && params[key] !== null && params[key] !== "") {
        search.set(key, String(params[key]));
      }
    });
    var suffix = search.toString();
    return path + (suffix ? "?" + suffix : "");
  }

  async function api(path, options) {
    var settings = Object.assign({}, options || {});
    if (window.PiStickAndroid && typeof window.PiStickAndroid.postMessage === "function") {
      var androidRequestId = String(++nextNativeRequest);
      var androidRequestBody = settings.body || {};
      if (typeof androidRequestBody === "string") {
        try { androidRequestBody = JSON.parse(androidRequestBody); }
        catch (error) { throw new Error("The app request body is invalid."); }
      }
      return new Promise(function (resolve, reject) {
        var timeout = window.setTimeout(function () {
          delete nativeRequests[androidRequestId];
          reject(new Error("PiStick took too long to complete the request. Try again."));
        }, 35000);
        nativeRequests[androidRequestId] = { resolve: resolve, reject: reject, timeout: timeout };
        window.PiStickAndroid.postMessage(JSON.stringify({
          secret: String(window.__PISTICK_ANDROID_SECRET__ || ""),
          id: androidRequestId,
          path: path,
          method: settings.method || "GET",
          body: androidRequestBody
        }));
      });
    }
    throw new Error("PiStick's Android bridge is unavailable. Restart or reinstall the app.");
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    ui.toast.textContent = String(message);
    ui.toast.classList.remove("hidden");
    state.toastTimer = window.setTimeout(function () {
      ui.toast.classList.add("hidden");
    }, 3600);
  }

  function setHeader(visible) {
    ui.header.classList.toggle("hidden", !visible);
  }

  function setActiveNav(view) {
    document.querySelectorAll(".nav-button").forEach(function (node) {
      node.classList.toggle("active", node.dataset.view === view);
    });
  }

  function activeProfileId() {
    return state.activeProfile ? state.activeProfile.id : "";
  }

  function loadingPage(message, headerVisible) {
    setHeader(Boolean(headerVisible));
    var page = element("section", "loading-page");
    var wrap = element("div");
    wrap.append(element("div", "spinner"), element("div", "", message || "Loading PiStick…"));
    page.append(wrap);
    ui.app.replaceChildren(page);
  }

  function errorPage(error, retry) {
    var page = element("section", "page");
    var card = element("div", "error-state");
    card.append(element("p", "eyebrow", "COULD NOT LOAD"), element("h1", "", "PiStick hit a problem"));
    card.append(element("p", "muted", error.message || String(error)));
    var row = element("div", "button-row");
    if (retry) row.append(button("Try again", "primary-button", retry));
    row.append(button("Settings", "secondary-button", function () { openSettings(false); }));
    card.append(row);
    page.append(card);
    ui.app.replaceChildren(page);
  }

  async function refreshProfiles() {
    var payload = await api("/api/profiles");
    state.profiles = payload.profiles || [];
    state.activeProfile = state.profiles.find(function (profile) {
      return profile.id === payload.active_profile;
    }) || null;
    ui.profileButton.textContent = state.activeProfile ? state.activeProfile.name : "Profiles";
    return payload;
  }

  function profileAvatar(profile, small) {
    var avatar = element("div", (small ? "manage-avatar " : "profile-avatar ") + "avatar-" + profile.avatar);
    avatar.textContent = String(profile.name || "P").trim().charAt(0).toUpperCase() || "P";
    return avatar;
  }

  function profileCard(profile) {
    var card = button("", "profile-card", async function () {
      try {
        await api("/api/profiles/" + encodeURIComponent(profile.id) + "/activate", { method: "POST" });
        await refreshProfiles();
        await renderHome();
      } catch (error) {
        showToast(error.message);
      }
    });
    card.setAttribute("aria-label", "Use profile " + profile.name);
    card.append(profileAvatar(profile, false), element("p", "", profile.name));
    return card;
  }

  function addProfileCard() {
    var card = button("", "profile-card", async function () {
      var name = window.prompt("New profile name:", "");
      if (name === null) return;
      try {
        await api("/api/profiles", { method: "POST", body: { name: name } });
        await refreshProfiles();
        renderProfiles(state.manageProfiles);
      } catch (error) {
        showToast(error.message);
      }
    });
    card.setAttribute("aria-label", "Add profile");
    card.append(element("div", "profile-avatar add-avatar", "+"), element("p", "", "Add Profile"));
    return card;
  }

  function manageProfileRow(profile) {
    var row = element("div", "manage-row");
    var input = element("input", "profile-name-input");
    input.value = profile.name;
    input.maxLength = 40;
    input.setAttribute("aria-label", "Name for " + profile.name);
    var save = button("Save", "secondary-button", async function () {
      try {
        await api("/api/profiles/" + encodeURIComponent(profile.id), {
          method: "PATCH",
          body: { name: input.value }
        });
        await refreshProfiles();
        showToast("Profile saved.");
        renderProfiles(true);
      } catch (error) {
        showToast(error.message);
      }
    });
    var remove = button("Delete", "danger-button", async function () {
      if (!window.confirm("Delete " + profile.name + " and its watch history?")) return;
      try {
        await api("/api/profiles/" + encodeURIComponent(profile.id), { method: "DELETE" });
        await refreshProfiles();
        renderProfiles(true);
      } catch (error) {
        showToast(error.message);
      }
    });
    row.append(profileAvatar(profile, true), input, save, remove);
    return row;
  }

  function renderProfiles(manage, returnToHome) {
    if (typeof returnToHome === "boolean") state.profilesReturnHome = returnToHome;
    state.currentView = "profiles";
    state.manageProfiles = Boolean(manage);
    setHeader(false);
    var page = element("section", "profiles-page");
    var panel = element("div", "profiles-panel");
    panel.append(element("p", "eyebrow", "PISTICK"));
    panel.append(element("h1", "", manage ? "Manage profiles" : "Who's watching?"));
    panel.append(element("p", "profiles-subtitle", manage ? "Names and watch histories are saved on this device." : "Choose a profile to continue."));

    if (manage) {
      var list = element("div", "manage-list");
      state.profiles.forEach(function (profile) { list.append(manageProfileRow(profile)); });
      panel.append(list);
      var manageButtons = element("div", "button-row");
      manageButtons.style.justifyContent = "center";
      manageButtons.append(button("Done", "primary-button", function () { renderProfiles(false); }));
      panel.append(manageButtons);
    } else {
      var grid = element("div", "profile-grid");
      state.profiles.forEach(function (profile) { grid.append(profileCard(profile)); });
      if (state.profiles.length < 8) grid.append(addProfileCard());
      panel.append(grid);
      panel.append(button("Manage Profiles", "secondary-button", function () { renderProfiles(true); }));
    }

    var settings = button("App Settings", "nav-button", function () { openSettings(false); });
    settings.style.marginTop = "18px";
    panel.append(settings);
    page.append(panel);
    ui.app.replaceChildren(page);
    window.setTimeout(focusFirst, 50);
  }

  function poster(media) {
    var wrap = element("div", "poster-wrap");
    var url = imageUrl(media.poster_path, FIRE_TV ? "w342" : "w500");
    if (url) {
      var image = element("img");
      image.src = url;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      image.addEventListener("error", function () {
        wrap.replaceChildren(element("div", "poster-fallback", titleOf(media)));
      });
      wrap.append(image);
    } else {
      wrap.append(element("div", "poster-fallback", titleOf(media)));
    }
    var watch = media.watch || {};
    if (watch.status === "finished") wrap.append(element("div", "watch-badge", "WATCHED"));
    if (watch.status === "in_progress") {
      var track = element("div", "progress-track");
      var fill = element("div", "progress-fill");
      fill.style.width = Math.max(3, Math.min(100, Number(watch.progress || 0) * 100)) + "%";
      track.append(fill);
      wrap.append(track);
    }
    return wrap;
  }

  function mediaCard(media) {
    var card = button("", "media-card", function () { openDetails(media); });
    card.setAttribute("aria-label", "Open " + titleOf(media));
    var copy = element("div", "card-copy");
    copy.append(element("p", "card-title", titleOf(media)));
    copy.append(element("p", "card-meta", mediaMeta(media)));
    card.append(poster(media), copy);
    card._pistickMedia = media;
    return card;
  }

  function mediaRow(row) {
    var section = element("section", "media-section");
    section.append(element("h2", "", row.title || "Explore"));
    var rail = element("div", "media-row");
    (row.items || []).forEach(function (media) { rail.append(mediaCard(media)); });
    if (!rail.childElementCount) rail.append(element("p", "muted", "Nothing to show here yet."));
    section.append(rail);
    return section;
  }

  function hero(media) {
    var section = element("section", "hero");
    var backdrop = imageUrl(media.backdrop_path, FIRE_TV ? "w1280" : "original");
    if (backdrop) {
      var image = element("img", "hero-backdrop");
      image.src = backdrop;
      image.alt = "";
      image.decoding = "async";
      image.fetchPriority = "high";
      section.append(image);
    }
    var content = element("div", "hero-content");
    content.append(element("p", "eyebrow", "FEATURED ON PISTICK"));
    content.append(element("h1", "", titleOf(media)));
    content.append(element("p", "meta-line", mediaMeta(media)));
    if (media.overview) content.append(element("p", "hero-overview", media.overview));
    var actions = element("div", "button-row");
    actions.append(button("View details", "primary-button", function () { openDetails(media); }));
    section.append(content);
    content.append(actions);
    return section;
  }

  async function renderHome() {
    if (!state.activeProfile) {
      renderProfiles(false, false);
      return;
    }
    state.currentView = "home";
    setHeader(true);
    setActiveNav("home");
    var token = ++state.loadToken;
    loadingPage("Building your home screen…", true);
    try {
      var payload = await api(query("/api/home", { profile_id: activeProfileId() }));
      if (token !== state.loadToken) return;
      var fragment = document.createDocumentFragment();
      if (payload.hero) fragment.append(hero(payload.hero));
      var rows = element("div", "content-rows");
      (payload.rows || []).forEach(function (row) { rows.append(mediaRow(row)); });
      if (!rows.childElementCount) rows.append(element("div", "empty-state", "No titles are available right now."));
      fragment.append(rows);
      ui.app.replaceChildren(fragment);
      scrollPageTop();
      window.setTimeout(focusHomeStart, 30);
    } catch (error) {
      if (token !== state.loadToken) return;
      errorPage(error, renderHome);
      if (!state.status.tmdb_configured) openSettings(true);
    }
  }

  function resultPage(title, subtitle, items) {
    var page = element("section", "page");
    var heading = element("div", "page-heading");
    var copy = element("div");
    copy.append(element("p", "eyebrow", subtitle), element("h1", "", title));
    heading.append(copy);
    page.append(heading);
    if (items && items.length) {
      var grid = element("div", "media-grid");
      items.forEach(function (media) { grid.append(mediaCard(media)); });
      page.append(grid);
    } else {
      var empty = element("div", "empty-state");
      empty.append(element("h2", "", "No results"), element("p", "muted", "Try a different title or search term."));
      page.append(empty);
    }
    ui.app.replaceChildren(page);
  }

  async function renderDiscover(kind) {
    if (!state.activeProfile) return renderProfiles(false, false);
    state.currentView = kind;
    setHeader(true);
    setActiveNav(kind);
    var token = ++state.loadToken;
    loadingPage(kind === "movie" ? "Loading movies…" : "Loading TV shows…", true);
    try {
      var payload = await api(query("/api/discover/" + kind, { profile_id: activeProfileId() }));
      if (token !== state.loadToken) return;
      resultPage(kind === "movie" ? "Movies" : "TV Shows", "POPULAR RIGHT NOW", payload.items || []);
      scrollPageTop();
      window.setTimeout(focusPageStart, 30);
    } catch (error) {
      if (token === state.loadToken) errorPage(error, function () { renderDiscover(kind); });
    }
  }

  async function renderSearch(searchText) {
    var cleaned = String(searchText || "").trim();
    if (!cleaned) return renderHome();
    if (!state.activeProfile) return renderProfiles(false, false);
    state.currentView = "search";
    setHeader(true);
    setActiveNav("");
    var token = ++state.loadToken;
    loadingPage("Searching for “" + cleaned + "”…", true);
    try {
      var payload = await api(query("/api/search", { q: cleaned, profile_id: activeProfileId() }));
      if (token !== state.loadToken) return;
      resultPage("Search results", "RESULTS FOR “" + cleaned.toUpperCase() + "”", payload.items || []);
      scrollPageTop();
      window.setTimeout(focusPageStart, 30);
    } catch (error) {
      if (token === state.loadToken) errorPage(error, function () { renderSearch(cleaned); });
    }
  }

  function trailerFor(media) {
    var videos = media.videos && Array.isArray(media.videos.results) ? media.videos.results : [];
    var youtube = videos.filter(function (video) {
      return video.site === "YouTube" && /^[A-Za-z0-9_-]{6,20}$/.test(String(video.key || ""));
    });
    var trailers = youtube.filter(function (video) { return video.type === "Trailer"; });
    var official = trailers.filter(function (video) { return Boolean(video.official); });
    return (official[0] || trailers[0] || youtube[0] || null);
  }

  async function watchAction(action, media, episode) {
    try {
      await api("/api/watch/action", {
        method: "POST",
        body: {
          profile_id: activeProfileId(),
          action: action,
          media: media,
          episode: episode || undefined
        }
      });
      showToast("Watch history updated.");
      await openDetails(media, true);
    } catch (error) {
      showToast(error.message);
    }
  }

  function detailsActions(media) {
    var row = element("div", "button-row");
    var watchText = media.media_type === "tv" ? "Watch show" : ((media.watch && media.watch.position_seconds) ? "Resume movie" : "Watch movie");
    row.append(button(watchText, "primary-button", function () {
      if (media.media_type === "tv") {
        var resume = media.resume_episode || { season_number: 1, episode_number: 1 };
        playResumeEpisode(media, resume);
      } else {
        startPlayback(media, null);
      }
    }));

    var trailer = trailerFor(media);
    if (trailer) {
      row.append(button("Play trailer", "secondary-button", function () {
        openTrailer(titleOf(media) + " — Trailer", trailer.key);
      }));
    }

    var watch = media.watch || {};
    if (watch.status === "finished") {
      row.append(button("Mark as unwatched", "secondary-button", function () {
        watchAction("unwatched", media);
      }));
    } else if (media.media_type === "movie") {
      row.append(button("Mark as finished", "secondary-button", function () {
        watchAction("finished", media);
      }));
    } else if (watch.status === "in_progress") {
      var resumeEpisode = media.resume_episode || { season_number: 1, episode_number: 1 };
      row.append(button("Mark episode finished", "secondary-button", function () {
        watchAction("episode_finished", media, {
          season_number: Number(resumeEpisode.season_number),
          episode_number: Number(resumeEpisode.episode_number),
          name: "Episode " + Number(resumeEpisode.episode_number)
        });
      }));
      row.append(button("Mark show finished", "secondary-button", function () {
        watchAction("show_finished", media);
      }));
    }
    return row;
  }

  function episodeStatus(episode) {
    var watch = episode.watch || {};
    if (watch.status === "finished") return "WATCHED";
    if (watch.status === "in_progress") return Math.round(Number(watch.progress || 0) * 100) + "%";
    return "PLAY";
  }

  function episodeCard(show, episode) {
    var card = button("", "episode-card", function () { startPlayback(show, episode); });
    card.setAttribute("aria-label", "Play episode " + episode.episode_number + ", " + (episode.name || ""));
    var stillUrl = imageUrl(episode.still_path, FIRE_TV ? "w300" : "w500");
    if (stillUrl) {
      var still = element("img", "episode-still");
      still.src = stillUrl;
      still.alt = "";
      still.loading = "lazy";
      still.decoding = "async";
      card.append(still);
    } else {
      card.append(element("div", "episode-still poster-fallback", "Episode " + episode.episode_number));
    }
    var copy = element("div", "episode-copy");
    copy.append(element("h4", "", episode.episode_number + ". " + (episode.name || "Episode " + episode.episode_number)));
    copy.append(element("p", "", episode.overview || "No episode description is available."));
    card.append(copy, element("div", "episode-status", episodeStatus(episode)));
    return card;
  }

  async function loadSeason(show, seasonNumber, target) {
    target.replaceChildren(element("div", "loading-page", "Loading episodes…"));
    try {
      var payload = await api(query("/api/tv/" + Number(show.id) + "/season/" + Number(seasonNumber), {
        profile_id: activeProfileId()
      }));
      var list = element("div", "episodes-list");
      (payload.season.episodes || []).forEach(function (episode) {
        list.append(episodeCard(show, episode));
      });
      if (!list.childElementCount) list.append(element("p", "muted", "No episodes were found for this season."));
      target.replaceChildren(list);
      return payload.season.episodes || [];
    } catch (error) {
      var failure = element("div", "error-state");
      failure.append(element("p", "muted", error.message));
      failure.append(button("Try again", "secondary-button", function () { loadSeason(show, seasonNumber, target); }));
      target.replaceChildren(failure);
      return [];
    }
  }

  function showEpisodePicker(media, body) {
    var seasons = Array.isArray(media.seasons) ? media.seasons.filter(function (season) {
      return Number(season.episode_count) > 0;
    }) : [];
    if (!seasons.length) return;
    seasons.sort(function (left, right) {
      var a = Number(left.season_number);
      var b = Number(right.season_number);
      if (a === 0) return 1;
      if (b === 0) return -1;
      return a - b;
    });
    var heading = element("div", "season-heading-row");
    heading.append(element("h3", "", "Episodes"));
    var select = element("select", "season-select");
    select.setAttribute("aria-label", "Season");
    seasons.forEach(function (season) {
      var option = element("option", "", season.name || "Season " + season.season_number);
      option.value = String(season.season_number);
      select.append(option);
    });
    var resume = media.resume_episode || {};
    if (seasons.some(function (season) { return Number(season.season_number) === Number(resume.season_number); })) {
      select.value = String(resume.season_number);
    }
    heading.append(select);
    var episodes = element("div");
    select.addEventListener("change", function () { loadSeason(media, Number(select.value), episodes); });
    body.append(heading, episodes);
    loadSeason(media, Number(select.value), episodes);
  }

  function renderDetails(media) {
    state.detailsMedia = media;
    var root = element("div");
    var backdrop = element("section", "details-backdrop");
    var image = imageUrl(media.backdrop_path, FIRE_TV ? "w1280" : "original");
    if (image) {
      var picture = element("img");
      picture.src = image;
      picture.alt = "";
      picture.decoding = "async";
      backdrop.append(picture);
    }
    var copy = element("div", "details-copy");
    copy.append(element("p", "eyebrow", media.media_type === "tv" ? "TV SERIES" : "MOVIE"));
    copy.append(element("h2", "", titleOf(media)));
    copy.append(element("p", "meta-line", mediaMeta(media)));
    copy.append(detailsActions(media));
    backdrop.append(copy);
    root.append(backdrop);
    var body = element("div", "details-body");
    body.append(element("p", "details-overview", media.overview || "No description is available."));
    if (media.media_type === "tv") showEpisodePicker(media, body);
    root.append(body);
    ui.detailsContent.replaceChildren(root);
    window.setTimeout(function () {
      var first = ui.detailsDialog.querySelector(".primary-button");
      if (first) first.focus();
    }, 40);
  }

  async function openDetails(media, refresh) {
    if (!ui.detailsDialog.open) {
      state.detailsReturnFocus = document.activeElement;
      ui.detailsDialog.showModal();
    }
    ui.detailsContent.replaceChildren(element("div", "loading-page", "Loading title details…"));
    try {
      var payload = await api(query("/api/media/" + encodeURIComponent(media.media_type) + "/" + Number(media.id), {
        profile_id: activeProfileId()
      }));
      renderDetails(payload.media);
      if (refresh && state.currentView === "home") {
        // The dialog remains open; Home is refreshed after it closes.
        ui.detailsDialog.dataset.refreshHome = "1";
      }
    } catch (error) {
      var card = element("div", "error-state");
      card.append(element("h2", "", "Could not load this title"));
      card.append(element("p", "muted", error.message));
      ui.detailsContent.replaceChildren(card);
    }
  }

  function closeDetails(restoreAfterClose) {
    if (!ui.detailsDialog.open) return;
    var returnFocus = state.detailsReturnFocus;
    ui.detailsDialog.close();
    state.detailsReturnFocus = null;
    if (restoreAfterClose !== false) restoreFocus(returnFocus);
  }

  function playResumeEpisode(show, resume) {
    var episode = {
      season_number: Number(resume.season_number || 1),
      episode_number: Number(resume.episode_number || 1),
      name: "Episode " + Number(resume.episode_number || 1)
    };
    startPlayback(show, episode);
  }

  async function startPlayback(media, episode) {
    try {
      var payload = await api("/api/play", {
        method: "POST",
        body: {
          profile_id: activeProfileId(),
          media: media,
          episode: episode || undefined
        }
      });
      var title = titleOf(media);
      if (episode) {
        title += " — S" + Number(episode.season_number) + ":E" + Number(episode.episode_number) + " — " + (episode.name || "Episode");
      }
      openPlayer(title, payload.embed_url, media, episode || null, false);
    } catch (error) {
      showToast(error.message);
    }
  }

  function openTrailer(title, key) {
    var origin = window.location.origin === "null" ? "https://app.pistick.local" : window.location.origin;
    var url = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(key)
      + "?autoplay=1&rel=0&cc_load_policy=0&enablejsapi=1&playsinline=1&origin="
      + encodeURIComponent(origin);
    openPlayer(title, url, null, null, true);
  }

  function openPlayer(title, url, media, episode, trailer) {
    var playerUrl = String(url);
    var isVideasyPlayer = /^https:\/\/player\.videasy\.(to|net)\//.test(playerUrl);
    var isYouTubePlayer = /^https:\/\/www\.youtube-nocookie\.com\//.test(playerUrl);
    if (!isVideasyPlayer && !isYouTubePlayer) {
      showToast("The player URL was rejected.");
      return;
    }
    var playerReturnFocus = state.detailsReturnFocus;
    closeDetails(false);
    state.player = {
      media: media,
      episode: episode,
      trailer: Boolean(trailer),
      position: 0,
      duration: 0,
      lastSavedAt: 0,
      saving: false,
      isVideasy: isVideasyPlayer,
      autoStartRequested: false,
      autoStartTimer: 0,
      returnFocus: playerReturnFocus
    };
    ui.playerTitle.textContent = title;
    if (isVideasyPlayer) {
      // Videasy rejects playback when its outer iframe has any sandbox
      // attribute, even when scripts and forms are explicitly allowed.
      // The URL allowlist above and cross-origin browser isolation still keep
      // the PiStick application and its on-device data separate.
      ui.playerFrame.removeAttribute("sandbox");
    } else {
      ui.playerFrame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-presentation allow-pointer-lock");
    }
    ui.playerOverlay.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    ui.playerFrame.src = playerUrl;
    window.requestAnimationFrame(function () { ui.playerFrame.focus(); });
  }

  function decodePlayerMessage(value) {
    var data = value;
    if (typeof data === "string") {
      try { data = JSON.parse(data); } catch (error) { return null; }
    }
    if (!data || typeof data !== "object") return null;
    if (data.type === "PLAYER_EVENT" && data.data && typeof data.data === "object") {
      return {
        position: Number(data.data.timestamp !== undefined ? data.data.timestamp : data.data.currentTime),
        duration: Number(data.data.duration)
      };
    }
    if (data.type === "pistick-playback-progress") {
      return {
        position: Number(data.currentTime !== undefined ? data.currentTime : data.position),
        duration: Number(data.duration)
      };
    }
    return null;
  }

  async function savePlayerProgress(force) {
    var player = state.player;
    if (!player || player.trailer || !player.media || player.saving) return;
    if (!(player.position >= 0) || !(player.duration > 0)) return;
    var now = Date.now();
    if (!force && now - player.lastSavedAt < 4500) return;
    player.saving = true;
    try {
      await api("/api/watch/progress", {
        method: "POST",
        body: {
          profile_id: activeProfileId(),
          media: player.media,
          episode: player.episode || undefined,
          position_seconds: player.position,
          duration_seconds: player.duration
        }
      });
      player.lastSavedAt = now;
    } catch (error) {
      if (force) showToast("Could not save playback progress.");
    } finally {
      player.saving = false;
    }
  }

  function closePlayer() {
    if (!state.player) return;
    var returnFocus = state.player.returnFocus;
    window.clearTimeout(state.player.autoStartTimer);
    savePlayerProgress(true);
    state.player = null;
    ui.playerFrame.src = "about:blank";
    ui.playerOverlay.classList.add("hidden");
    document.body.style.overflow = "";
    restoreFocus(returnFocus);
  }

  function postPlayerCommand(action, extra) {
    if (!state.player) return;
    var message = Object.assign({ type: "pistick-media-command", action: action }, extra || {});
    try { ui.playerFrame.contentWindow.postMessage(message, "*"); } catch (error) { /* cross-origin-safe best effort */ }
  }

  function openSettings(required) {
    if (state.textEntry) finishTextEntry();
    ui.settingsMessage.textContent = state.status && state.status.tmdb_configured ? "A credential is already saved. Paste a new one only to replace it." : "A TMDB credential is required before titles can load.";
    ui.settingsMessage.classList.remove("success");
    ui.settingsToken.value = "";
    ui.settingsDialog.dataset.required = required ? "1" : "0";
    if (!ui.settingsDialog.open) {
      state.settingsReturnFocus = document.activeElement;
      ui.settingsDialog.showModal();
    }
    window.setTimeout(function () { ui.settingsToken.focus(); }, 40);
  }

  function closeSettings() {
    if (ui.settingsDialog.dataset.required === "1") return;
    if (!ui.settingsDialog.open) return;
    if (state.textEntry) finishTextEntry();
    ui.settingsDialog.close();
    restoreFocus(state.settingsReturnFocus);
    state.settingsReturnFocus = null;
  }

  function restoreFocus(node) {
    window.setTimeout(function () {
      if (node && node.isConnected && typeof node.focus === "function") focusAndReveal(node);
      else focusFirst();
    }, 30);
  }

  function scrollPageTop() {
    window.scrollTo({ top: 0, behavior: FIRE_TV ? "auto" : "smooth" });
  }

  function focusRoot() {
    if (state.player && ui.playerOverlay) return ui.playerOverlay;
    if (ui.settingsDialog && ui.settingsDialog.open) return ui.settingsDialog;
    if (ui.detailsDialog && ui.detailsDialog.open) return ui.detailsDialog;
    return document;
  }

  function visibleFocusable() {
    return Array.from(focusRoot().querySelectorAll("button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")).filter(function (node) {
      return node.getClientRects().length > 0 && node.offsetWidth > 0 && node.offsetHeight > 0;
    });
  }

  function focusAndReveal(node, block, inline) {
    if (!node || typeof node.focus !== "function") return false;
    node.focus({ preventScroll: true });
    if (typeof node.scrollIntoView === "function" && !node.closest(".site-header")) {
      node.scrollIntoView({
        behavior: FIRE_TV ? "auto" : "smooth",
        block: block || "nearest",
        inline: inline || "nearest"
      });
    }
    return true;
  }

  function focusFirst() {
    var nodes = visibleFocusable();
    if (nodes.length) focusAndReveal(nodes[0]);
  }

  function focusHomeStart() {
    var featured = document.querySelector(".hero .primary-button");
    if (featured) focusAndReveal(featured, "nearest", "nearest");
    else focusPageStart();
  }

  function focusPageStart() {
    var first = document.querySelector("#app .media-card, #app button, #app input, #app select");
    if (first) focusAndReveal(first, "nearest", "nearest");
  }

  function headerControls() {
    return Array.from(ui.header.querySelectorAll("button:not([disabled]), input:not([disabled])")).filter(function (node) {
      return node.getClientRects().length > 0;
    });
  }

  function activeHeaderTarget() {
    return ui.header.querySelector(".nav-button.active") || ui.brandButton;
  }

  function firstContentTarget() {
    if (state.currentView === "home") {
      return document.querySelector(".hero .primary-button") || document.querySelector(".media-row .media-card");
    }
    return document.querySelector("#app .media-card, #app button, #app input, #app select");
  }

  function moveHeaderFocus(current, direction) {
    var controls = headerControls();
    var index = controls.indexOf(current);
    if (index < 0) return false;
    if (direction === "left" && index > 0) return focusAndReveal(controls[index - 1]);
    if (direction === "right" && index + 1 < controls.length) return focusAndReveal(controls[index + 1]);
    if (direction === "down") return focusAndReveal(firstContentTarget(), "center", "nearest");
    return true;
  }

  function closestCardAtScreenX(rail, screenX) {
    var cards = Array.from(rail.querySelectorAll(".media-card"));
    var best = null;
    var distance = Infinity;
    cards.forEach(function (card) {
      var rect = card.getBoundingClientRect();
      var candidateDistance = Math.abs(rect.left + rect.width / 2 - screenX);
      if (candidateDistance < distance) {
        best = card;
        distance = candidateDistance;
      }
    });
    return best;
  }

  function moveRailFocus(current, direction) {
    var rail = current.closest(".media-row");
    if (!rail) return false;
    var cards = Array.from(rail.querySelectorAll(".media-card"));
    var index = cards.indexOf(current);
    if (index < 0 || !cards.length) return false;
    if (direction === "left" || direction === "right") {
      var next = direction === "right"
        ? (index + 1) % cards.length
        : (index - 1 + cards.length) % cards.length;
      return focusAndReveal(cards[next], "nearest", "center");
    }

    var sections = Array.from(document.querySelectorAll(".content-rows .media-section"));
    var section = rail.closest(".media-section");
    var sectionIndex = sections.indexOf(section);
    if (direction === "up" && sectionIndex === 0) {
      return focusAndReveal(document.querySelector(".hero .primary-button"), "nearest", "nearest");
    }
    var targetIndex = direction === "up" ? sectionIndex - 1 : sectionIndex + 1;
    if (targetIndex < 0 || targetIndex >= sections.length) return true;
    var currentRect = current.getBoundingClientRect();
    var targetRail = sections[targetIndex].querySelector(".media-row");
    var target = targetRail && closestCardAtScreenX(targetRail, currentRect.left + currentRect.width / 2);
    return focusAndReveal(target, "center", "center");
  }

  function gridRows(grid) {
    var rows = [];
    Array.from(grid.querySelectorAll(".media-card")).forEach(function (card) {
      var top = card.offsetTop;
      var row = rows.find(function (candidate) { return Math.abs(candidate.top - top) < 4; });
      if (!row) {
        row = { top: top, cards: [] };
        rows.push(row);
      }
      row.cards.push(card);
    });
    rows.sort(function (left, right) { return left.top - right.top; });
    return rows;
  }

  function moveGridFocus(current, direction) {
    var grid = current.closest(".media-grid");
    if (!grid) return false;
    var rows = gridRows(grid);
    var rowIndex = rows.findIndex(function (row) { return row.cards.indexOf(current) >= 0; });
    if (rowIndex < 0) return false;
    var row = rows[rowIndex];
    var column = row.cards.indexOf(current);
    if (direction === "left" || direction === "right") {
      var horizontal = direction === "left" ? column - 1 : column + 1;
      if (horizontal >= 0 && horizontal < row.cards.length) {
        return focusAndReveal(row.cards[horizontal], "nearest", "center");
      }
      return true;
    }
    if (direction === "up" && rowIndex === 0) {
      return focusAndReveal(activeHeaderTarget());
    }
    var targetRowIndex = direction === "up" ? rowIndex - 1 : rowIndex + 1;
    if (targetRowIndex < 0 || targetRowIndex >= rows.length) return true;
    var currentRect = current.getBoundingClientRect();
    var target = rows[targetRowIndex].cards.reduce(function (best, card) {
      if (!best) return card;
      var targetX = currentRect.left + currentRect.width / 2;
      var bestRect = best.getBoundingClientRect();
      var cardRect = card.getBoundingClientRect();
      return Math.abs(cardRect.left + cardRect.width / 2 - targetX)
        < Math.abs(bestRect.left + bestRect.width / 2 - targetX) ? card : best;
    }, null);
    return focusAndReveal(target, "center", "center");
  }

  function moveEpisodeFocus(current, direction) {
    if (direction !== "up" && direction !== "down") return false;
    var cards = Array.from(current.closest(".episodes-list").querySelectorAll(".episode-card"));
    var index = cards.indexOf(current);
    if (direction === "up" && index === 0) {
      return focusAndReveal(ui.detailsDialog.querySelector(".season-select, .primary-button"), "nearest", "nearest");
    }
    var target = direction === "up" ? index - 1 : index + 1;
    if (target < 0 || target >= cards.length) return true;
    return focusAndReveal(cards[target], "center", "nearest");
  }

  function spatialFocus(current, direction) {
    var candidates = visibleFocusable();
    if (!candidates.length) return false;
    if (!current || candidates.indexOf(current) < 0) return focusAndReveal(candidates[0]);
    var origin = current.getBoundingClientRect();
    var ox = origin.left + origin.width / 2;
    var oy = origin.top + origin.height / 2;
    var best = null;
    var bestScore = Infinity;
    candidates.forEach(function (candidate) {
      if (candidate === current) return;
      var rect = candidate.getBoundingClientRect();
      var dx = rect.left + rect.width / 2 - ox;
      var dy = rect.top + rect.height / 2 - oy;
      if (direction === "left" && dx >= -3) return;
      if (direction === "right" && dx <= 3) return;
      if (direction === "up" && dy >= -3) return;
      if (direction === "down" && dy <= 3) return;
      var primary = (direction === "left" || direction === "right") ? Math.abs(dx) : Math.abs(dy);
      var secondary = (direction === "left" || direction === "right") ? Math.abs(dy) : Math.abs(dx);
      var score = primary + secondary * 2.6;
      if (score < bestScore) {
        bestScore = score;
        best = candidate;
      }
    });
    return focusAndReveal(best);
  }

  function moveFocus(direction) {
    var current = document.activeElement;
    if (current && current.closest && current.closest(".site-header")) return moveHeaderFocus(current, direction);
    if (current && current.classList && current.classList.contains("media-card")) {
      if (current.closest(".media-row")) return moveRailFocus(current, direction);
      if (current.closest(".media-grid")) return moveGridFocus(current, direction);
    }
    if (current && current.classList && current.classList.contains("episode-card")) {
      if (moveEpisodeFocus(current, direction)) return true;
    }
    if (current && current.closest && current.closest(".hero")) {
      if (direction === "up") return focusAndReveal(activeHeaderTarget());
      if (direction === "down") return focusAndReveal(document.querySelector(".media-row .media-card"), "center", "center");
    }
    return spatialFocus(current, direction);
  }

  function backAction() {
    if (state.textEntry) {
      finishTextEntry();
      return true;
    }
    if (state.player) {
      closePlayer();
      return true;
    }
    if (ui.settingsDialog.open) {
      closeSettings();
      return true;
    }
    if (ui.detailsDialog.open) {
      closeDetails();
      return true;
    }
    if (state.manageProfiles) {
      renderProfiles(false);
      return true;
    }
    if (state.currentView === "profiles") {
      if (state.profilesReturnHome && state.activeProfile) {
        renderHome();
        return true;
      }
      return false;
    }
    if (state.currentView !== "home" && state.activeProfile) {
      renderHome();
      return true;
    }
    return false;
  }

  function playerDirectional(direction) {
    if (direction === "left") postPlayerCommand("seek-relative", { offsetSeconds: -10 });
    if (direction === "right") postPlayerCommand("seek-relative", { offsetSeconds: 10 });
  }

  function isTextEntryTarget(target) {
    return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  }

  function beginTextEntry(target) {
    if (!isTextEntryTarget(target)) return false;
    state.textEntry = target;
    target.focus();
    if (typeof target.setSelectionRange === "function") {
      var end = target.value.length;
      target.setSelectionRange(end, end);
    }
    target.click();
    nativeUi("showKeyboard");
    return true;
  }

  function finishTextEntry() {
    state.textEntry = null;
    nativeUi("hideKeyboard");
  }

  function adjustTextCaret(target, direction) {
    if (!isTextEntryTarget(target) || state.textEntry !== target) return false;
    if (direction !== "left" && direction !== "right") return false;
    var start = Number(target.selectionStart || 0);
    var end = Number(target.selectionEnd || start);
    if ((direction === "left" && start === 0 && end === 0)
        || (direction === "right" && start === target.value.length && end === target.value.length)) {
      finishTextEntry();
      return false;
    }
    var next = direction === "left" ? Math.max(0, start - 1) : Math.min(target.value.length, end + 1);
    target.setSelectionRange(next, next);
    return true;
  }

  function adjustSelect(target, direction) {
    if (!(target instanceof HTMLSelectElement)) return false;
    var delta = (direction === "left" || direction === "up") ? -1
      : ((direction === "right" || direction === "down") ? 1 : 0);
    if (!delta) return false;
    var next = Math.max(0, Math.min(target.options.length - 1, target.selectedIndex + delta));
    if (next === target.selectedIndex) return false;
    target.selectedIndex = next;
    target.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function remoteAction(action) {
    var direction = { left: "left", right: "right", up: "up", down: "down" }[action];
    if (direction) {
      var target = document.activeElement;
      if (state.player) {
        if (!nativePlayerKey(direction) && (direction === "left" || direction === "right")) {
          playerDirectional(direction);
        }
      } else if (!adjustTextCaret(target, direction) && !adjustSelect(target, direction)) {
        moveFocus(direction);
      }
      return true;
    }
    if (action === "select") {
      if (state.player) {
        if (!nativePlayerKey("select")) postPlayerCommand("toggle");
        return true;
      }
      var active = document.activeElement;
      if (beginTextEntry(active)) return true;
      if (active && typeof active.click === "function") active.click();
      else focusFirst();
      return true;
    }
    if (action === "back") return backAction();
    if (action === "menu") {
      if (state.player) postPlayerCommand("subtitles-english-toggle");
      else openSettings(false);
      return true;
    }
    if (action === "play-pause" && state.player) {
      if (!nativePlayerKey("play-pause")) postPlayerCommand("toggle");
      return true;
    }
    if ((action === "play" || action === "pause") && state.player) {
      if (!nativePlayerKey(action)) postPlayerCommand(action);
      return true;
    }
    if (action === "rewind" && state.player) {
      if (!nativePlayerKey("rewind")) playerDirectional("left");
      return true;
    }
    if (action === "fast-forward" && state.player) {
      if (!nativePlayerKey("fast-forward")) playerDirectional("right");
      return true;
    }
    if (action === "stop" && state.player) {
      if (!nativePlayerKey("stop")) closePlayer();
      return true;
    }
    return false;
  }

  window.PiStickFireTV = Object.freeze({
    handle: function (action) {
      try { return remoteAction(String(action || "")); }
      catch (error) { return true; }
    }
  });

  function setupKeyboard() {
    document.addEventListener("keydown", function (event) {
      var target = event.target;
      var typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
      if (event.key === "Escape") {
        event.preventDefault();
        backAction();
        return;
      }
      if (typing) return;
      if (event.key === "/" && !state.player) {
        event.preventDefault();
        ui.searchInput.focus();
        return;
      }
      var directions = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
      var direction = directions[event.key];
      if (direction) {
        event.preventDefault();
        if (state.player && (direction === "left" || direction === "right")) playerDirectional(direction);
        else moveFocus(direction);
      }
    });
  }

  function gamepadPressed(gamepad, index) {
    return Boolean(gamepad.buttons[index] && gamepad.buttons[index].pressed);
  }

  function gamepadEdge(gamepad, name, pressed, callback) {
    var key = gamepad.index + ":" + name;
    var previous = Boolean(state.gamepadPrevious[key]);
    state.gamepadPrevious[key] = Boolean(pressed);
    if (pressed && !previous) callback();
  }

  function pollGamepads(timestamp) {
    var gamepads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
    gamepads.forEach(function (gamepad) {
      gamepadEdge(gamepad, "a", gamepadPressed(gamepad, 0), function () { remoteAction("select"); });
      gamepadEdge(gamepad, "b", gamepadPressed(gamepad, 1), backAction);
      gamepadEdge(gamepad, "x", gamepadPressed(gamepad, 2), function () {
        if (state.player) postPlayerCommand("subtitles-english-toggle");
      });

      if (timestamp < state.gamepadCooldown) return;
      var horizontal = Number(gamepad.axes[0] || 0);
      var vertical = Number(gamepad.axes[1] || 0);
      var direction = null;
      if (gamepadPressed(gamepad, 14) || horizontal < -0.62) direction = "left";
      else if (gamepadPressed(gamepad, 15) || horizontal > 0.62) direction = "right";
      else if (gamepadPressed(gamepad, 12) || vertical < -0.62) direction = "up";
      else if (gamepadPressed(gamepad, 13) || vertical > 0.62) direction = "down";
      if (direction) {
        state.gamepadCooldown = timestamp + 190;
        if (state.player && (direction === "left" || direction === "right")) playerDirectional(direction);
        else moveFocus(direction);
      }
    });
    state.gamepadFrame = gamepads.length ? window.requestAnimationFrame(pollGamepads) : 0;
  }

  function startGamepadPolling() {
    if (!state.gamepadFrame) state.gamepadFrame = window.requestAnimationFrame(pollGamepads);
  }

  async function bootstrap() {
    ui.header = document.getElementById("site-header");
    ui.app = document.getElementById("app");
    ui.brandButton = document.getElementById("brand-button");
    ui.searchForm = document.getElementById("search-form");
    ui.searchInput = document.getElementById("search-input");
    ui.profileButton = document.getElementById("profile-button");
    ui.settingsButton = document.getElementById("settings-button");
    ui.detailsDialog = document.getElementById("details-dialog");
    ui.detailsContent = document.getElementById("details-content");
    ui.detailsClose = document.getElementById("details-close");
    ui.settingsDialog = document.getElementById("settings-dialog");
    ui.settingsForm = document.getElementById("settings-form");
    ui.settingsToken = document.getElementById("tmdb-token");
    ui.settingsMessage = document.getElementById("settings-message");
    ui.settingsClose = document.getElementById("settings-close");
    ui.playerOverlay = document.getElementById("player-overlay");
    ui.playerTitle = document.getElementById("player-title");
    ui.playerFrame = document.getElementById("player-frame");
    ui.playerClose = document.getElementById("player-close");
    ui.playerFullscreen = document.getElementById("player-fullscreen");
    ui.toast = document.getElementById("toast");

    ui.brandButton.addEventListener("click", renderHome);
    document.querySelectorAll(".nav-button").forEach(function (node) {
      node.addEventListener("click", function () {
        if (node.dataset.view === "home") renderHome();
        else renderDiscover(node.dataset.view);
      });
    });
    ui.searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      finishTextEntry();
      renderSearch(ui.searchInput.value);
    });
    ui.profileButton.addEventListener("click", function () { renderProfiles(false, true); });
    ui.settingsButton.addEventListener("click", function () { openSettings(false); });
    ui.detailsClose.addEventListener("click", closeDetails);
    ui.settingsClose.addEventListener("click", closeSettings);
    ui.playerClose.addEventListener("click", closePlayer);
    ui.playerFrame.addEventListener("load", function () {
      var player = state.player;
      if (!player || !player.isVideasy || player.autoStartRequested) return;
      player.autoStartRequested = true;
      player.autoStartTimer = window.setTimeout(function () {
        if (state.player !== player) return;
        ui.playerFrame.focus();
        nativeUi("requestPlayerAutostart");
      }, 2200);
    });
    ui.playerFullscreen.addEventListener("click", function () {
      var target = ui.playerOverlay;
      if (document.fullscreenElement) document.exitFullscreen();
      else if (target.requestFullscreen) target.requestFullscreen();
    });
    ui.detailsDialog.addEventListener("close", function () {
      if (ui.detailsDialog.dataset.refreshHome === "1") {
        delete ui.detailsDialog.dataset.refreshHome;
        if (state.currentView === "home") renderHome();
      }
    });
    ui.detailsDialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      closeDetails();
    });
    ui.settingsDialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      closeSettings();
    });
    ui.settingsForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      finishTextEntry();
      var token = ui.settingsToken.value.trim();
      ui.settingsMessage.textContent = "Validating with TMDB…";
      ui.settingsMessage.classList.remove("success");
      try {
        await api("/api/settings/tmdb", { method: "POST", body: { token: token } });
        state.status = await api("/api/status");
        ui.settingsToken.value = "";
        ui.settingsMessage.textContent = "Credential saved securely on this device.";
        ui.settingsMessage.classList.add("success");
        ui.settingsDialog.dataset.required = "0";
        window.setTimeout(function () {
          closeSettings();
          if (state.activeProfile) renderHome();
        }, 650);
      } catch (error) {
        ui.settingsMessage.textContent = error.message;
      }
    });
    [ui.searchInput, ui.settingsToken].forEach(function (input) {
      input.addEventListener("blur", function () {
        if (state.textEntry === input) state.textEntry = null;
      });
    });

    window.addEventListener("message", function (event) {
      if (!state.player || state.player.trailer) return;
      if (event.origin !== "https://player.videasy.to" && event.origin !== "https://player.videasy.net") return;
      var progress = decodePlayerMessage(event.data);
      if (!progress || !(progress.position >= 0) || !(progress.duration > 0)) return;
      state.player.position = progress.position;
      state.player.duration = progress.duration;
      if (progress.position > 0) {
        window.clearTimeout(state.player.autoStartTimer);
        state.player.autoStartTimer = 0;
      }
      savePlayerProgress(false);
    });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) savePlayerProgress(true);
    });
    window.addEventListener("gamepadconnected", function (event) {
      showToast("Controller connected: " + (event.gamepad.id || "Gamepad"));
      focusFirst();
      startGamepadPolling();
    });
    setupKeyboard();
    if (navigator.getGamepads && Array.from(navigator.getGamepads()).some(Boolean)) {
      startGamepadPolling();
    }

    loadingPage("Starting PiStick…", false);
    try {
      state.status = await api("/api/status");
      await refreshProfiles();
      if (!state.status.tmdb_configured) openSettings(true);
      if (state.activeProfile) await renderHome();
      else renderProfiles(false, false);
    } catch (error) {
      errorPage(error, bootstrap);
    }
  }

  document.addEventListener("DOMContentLoaded", bootstrap);
})();
