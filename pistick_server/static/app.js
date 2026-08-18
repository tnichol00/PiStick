(function () {
  "use strict";

  var requestedPiMode = new URLSearchParams(window.location.search).get("platform") === "pi-zero-w";
  document.documentElement.classList.toggle("pi-zero-w", requestedPiMode);

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
    gamepadPrevious: {},
    gamepadCooldown: 0,
    lowMemory: requestedPiMode,
    keyboardSubmit: null,
    keyboardReturnFocus: null,
    keyboardLayout: "letters",
    keyboardUpper: false,
    keyboardTrim: true,
    keyboardSubmitLabel: "Done",
    systemStatus: null
  };

  var ui = {};

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

  function motionBehavior() {
    return state.lowMemory ? "auto" : "smooth";
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
    var headers = Object.assign({ "Accept": "application/json" }, settings.headers || {});
    if (settings.body && typeof settings.body !== "string") {
      settings.body = JSON.stringify(settings.body);
      headers["Content-Type"] = "application/json";
    }
    if (settings.method && settings.method !== "GET") headers["X-PiStick-Request"] = "1";
    settings.headers = headers;
    var response;
    try {
      response = await fetch(path, settings);
    } catch (error) {
      throw new Error("PiStick Server is not responding. Restart it and try again.");
    }
    var payload;
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok) throw new Error(payload.error || "Request failed (" + response.status + ").");
    return payload;
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
    if (state.status && state.status.local_control) {
      row.append(button("System Settings", "secondary-button", openSettings));
    }
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
      openControllerKeyboard("New profile name", "", "Add profile", async function (name) {
        try {
          await api("/api/profiles", { method: "POST", body: { name: name } });
          await refreshProfiles();
          renderProfiles(state.manageProfiles);
        } catch (error) {
          showToast(error.message);
        }
      });
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

  function renderProfiles(manage) {
    state.currentView = "profiles";
    state.manageProfiles = Boolean(manage);
    setHeader(false);
    var page = element("section", "profiles-page");
    var panel = element("div", "profiles-panel");
    panel.append(element("p", "eyebrow", "PISTICK SERVER"));
    panel.append(element("h1", "", manage ? "Manage profiles" : "Who's watching?"));
    panel.append(element("p", "profiles-subtitle", manage ? "Names and watch histories are saved on this Pi." : "Choose a profile to continue."));

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
      var profileActions = element("div", "profile-actions");
      profileActions.append(button("Manage Profiles", "secondary-button", function () { renderProfiles(true); }));
      if (state.status && state.status.local_control) {
        profileActions.append(button("Settings", "secondary-button", openSettings));
      }
      panel.append(profileActions);
    }
    page.append(panel);
    ui.app.replaceChildren(page);
    window.setTimeout(focusFirst, 50);
  }

  function poster(media) {
    var wrap = element("div", "poster-wrap");
    var url = imageUrl(media.poster_path, state.lowMemory ? "w342" : "w500");
    if (url) {
      var image = element("img");
      image.src = url;
      image.alt = "";
      image.loading = "lazy";
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
    var backdrop = imageUrl(media.backdrop_path, state.lowMemory ? "w780" : "original");
    if (backdrop) {
      var image = element("img", "hero-backdrop");
      image.src = backdrop;
      image.alt = "";
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
      renderProfiles(false);
      return;
    }
    state.currentView = "home";
    if (state.status && !state.status.tmdb_configured) {
      renderSshSetupPage();
      return;
    }
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
      window.scrollTo({ top: 0, behavior: motionBehavior() });
    } catch (error) {
      if (token !== state.loadToken) return;
      errorPage(error, renderHome);
    }
  }

  function renderSshSetupPage() {
    state.currentView = "setup";
    setHeader(false);
    var page = element("section", "profiles-page");
    var panel = element("div", "profiles-panel setup-panel");
    panel.append(element("p", "eyebrow", "ONE SSH STEP REQUIRED"));
    panel.append(element("h1", "", "Add your TMDB token"));
    panel.append(element("p", "profiles-subtitle", "For security, the token cannot be entered or changed in a web browser."));
    var command = element("code", "setup-command", "sudo pistick-configure-tmdb");
    panel.append(command);
    var actions = element("div", "profile-actions");
    actions.append(button("Check again", "primary-button", async function () {
      state.status = await api("/api/status");
      if (state.status.tmdb_configured) renderHome();
      else showToast("The TMDB token is not configured yet.");
    }));
    actions.append(button("Profiles", "secondary-button", function () { renderProfiles(false); }));
    if (state.status && state.status.local_control) {
      actions.append(button("Settings", "secondary-button", openSettings));
    }
    panel.append(actions);
    page.append(panel);
    ui.app.replaceChildren(page);
    window.setTimeout(focusFirst, 40);
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
    if (!state.activeProfile) return renderProfiles(false);
    state.currentView = kind;
    setHeader(true);
    setActiveNav(kind);
    var token = ++state.loadToken;
    loadingPage(kind === "movie" ? "Loading movies…" : "Loading TV shows…", true);
    try {
      var payload = await api(query("/api/discover/" + kind, { profile_id: activeProfileId() }));
      if (token !== state.loadToken) return;
      resultPage(kind === "movie" ? "Movies" : "TV Shows", "POPULAR RIGHT NOW", payload.items || []);
      window.scrollTo({ top: 0, behavior: motionBehavior() });
    } catch (error) {
      if (token === state.loadToken) errorPage(error, function () { renderDiscover(kind); });
    }
  }

  async function renderSearch(searchText) {
    var cleaned = String(searchText || "").trim();
    if (!cleaned) return renderHome();
    if (!state.activeProfile) return renderProfiles(false);
    state.currentView = "search";
    setHeader(true);
    setActiveNav("");
    var token = ++state.loadToken;
    loadingPage("Searching for “" + cleaned + "”…", true);
    try {
      var payload = await api(query("/api/search", { q: cleaned, profile_id: activeProfileId() }));
      if (token !== state.loadToken) return;
      resultPage("Search results", "RESULTS FOR “" + cleaned.toUpperCase() + "”", payload.items || []);
      window.scrollTo({ top: 0, behavior: motionBehavior() });
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
    var stillUrl = imageUrl(episode.still_path, state.lowMemory ? "w300" : "w500");
    if (stillUrl) {
      var still = element("img", "episode-still");
      still.src = stillUrl;
      still.alt = "";
      still.loading = "lazy";
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
    var image = imageUrl(media.backdrop_path, state.lowMemory ? "w780" : "original");
    if (image) {
      var picture = element("img");
      picture.src = image;
      picture.alt = "";
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
    if (!ui.detailsDialog.open) ui.detailsDialog.showModal();
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

  function closeDetails() {
    if (ui.detailsDialog.open) ui.detailsDialog.close();
  }

  async function playResumeEpisode(show, resume) {
    var holder = document.createElement("div");
    var episodes = await loadSeason(show, Number(resume.season_number || 1), holder);
    var episode = episodes.find(function (candidate) {
      return Number(candidate.episode_number) === Number(resume.episode_number || 1);
    }) || {
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
    var url = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(key) + "?autoplay=1&rel=0&modestbranding=1&cc_load_policy=0";
    openPlayer(title, url, null, null, true);
  }

  function openPlayer(title, url, media, episode, trailer) {
    if (!/^https:\/\/(player\.videasy\.(to|net)|www\.youtube-nocookie\.com)\//.test(String(url))) {
      showToast("The player URL was rejected.");
      return;
    }
    closeDetails();
    state.player = {
      media: media,
      episode: episode,
      trailer: Boolean(trailer),
      position: 0,
      duration: 0,
      lastSavedAt: 0,
      saving: false
    };
    ui.playerTitle.textContent = title;
    ui.playerFrame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-presentation allow-pointer-lock");
    ui.playerFrame.src = url;
    ui.playerOverlay.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    ui.playerClose.focus();
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

  async function closePlayer() {
    if (!state.player) return;
    await savePlayerProgress(true);
    state.player = null;
    ui.playerFrame.src = "about:blank";
    ui.playerOverlay.classList.add("hidden");
    document.body.style.overflow = "";
    if (state.currentView === "home") renderHome();
  }

  function postPlayerCommand(action, extra) {
    if (!state.player) return;
    var message = Object.assign({ type: "pistick-media-command", action: action }, extra || {});
    try { ui.playerFrame.contentWindow.postMessage(message, "*"); } catch (error) { /* cross-origin-safe best effort */ }
  }

  function settingsMessage(message, success) {
    ui.settingsMessage.textContent = String(message || "");
    ui.settingsMessage.classList.toggle("success", Boolean(success));
  }

  function emptyDeviceMessage(message) {
    return element("div", "device-empty", message);
  }

  function deviceRow(title, detail, actionLabel, onAction) {
    var row = element("div", "device-row");
    var copy = element("div", "device-copy");
    copy.append(element("strong", "", title), element("span", "muted", detail));
    row.append(copy);
    if (actionLabel && onAction) row.append(button(actionLabel, "secondary-button", onAction));
    return row;
  }

  function renderWiredControllers(controllers) {
    ui.wiredList.replaceChildren();
    if (!controllers || !controllers.length) {
      ui.wiredList.append(emptyDeviceMessage("No wired controller detected."));
      return;
    }
    controllers.forEach(function (controller) {
      ui.wiredList.append(deviceRow(controller.name || "USB controller", controller.handlers || "Connected"));
    });
  }

  function renderBluetoothDevices(devices) {
    ui.bluetoothList.replaceChildren();
    if (!devices || !devices.length) {
      ui.bluetoothList.append(emptyDeviceMessage("No paired Bluetooth controllers found."));
      return;
    }
    devices.forEach(function (device) {
      var status = device.connected ? "Connected" : (device.paired ? "Paired" : device.address);
      var action = device.connected ? "" : (device.paired ? "Connect" : "Pair");
      ui.bluetoothList.append(deviceRow(device.name || device.address, status, action, action ? function () {
        pairBluetoothController(device);
      } : null));
    });
  }

  function renderSystemStatus(payload) {
    state.systemStatus = payload;
    ui.lanUrl.textContent = payload.lan_url || "http://pistick.local";
    ui.lanStatus.textContent = payload.lan_enabled ? "Available to devices on this Wi-Fi." : "Only the HDMI screen can connect.";
    ui.lanToggle.textContent = payload.lan_enabled ? "Turn off" : "Turn on";
    ui.lanToggle.dataset.enabled = payload.lan_enabled ? "1" : "0";

    var wifi = payload.wifi || {};
    if (!wifi.available) ui.wifiStatus.textContent = "Wi-Fi controls are unavailable.";
    else if (wifi.connected) {
      ui.wifiStatus.textContent = "Connected to " + wifi.ssid + (wifi.ipv4 ? " · " + wifi.ipv4 : "");
    } else ui.wifiStatus.textContent = "Not connected.";

    var bluetooth = payload.bluetooth || {};
    ui.bluetoothStatus.textContent = bluetooth.powered ? "Bluetooth is on." : "Bluetooth is off or unavailable.";
    renderBluetoothDevices(bluetooth.devices || []);
    renderWiredControllers(payload.wired_controllers || []);
  }

  async function refreshSystemStatus() {
    settingsMessage("Refreshing device status…", false);
    try {
      var payload = await api("/api/system/status");
      renderSystemStatus(payload);
      settingsMessage("", false);
    } catch (error) {
      settingsMessage(error.message, false);
    }
  }

  async function openSettings() {
    if (!state.status || !state.status.local_control) {
      showToast("System settings are available only on the Pi HDMI screen.");
      return;
    }
    if (!ui.settingsDialog.open) ui.settingsDialog.showModal();
    await refreshSystemStatus();
  }

  function closeSettings() {
    if (ui.settingsDialog.open) ui.settingsDialog.close();
  }

  async function toggleLanAccess() {
    var enabled = !(state.systemStatus && state.systemStatus.lan_enabled);
    ui.lanToggle.disabled = true;
    settingsMessage((enabled ? "Turning on" : "Turning off") + " access for other devices…", false);
    try {
      var payload = await api("/api/system/lan", { method: "POST", body: { enabled: enabled } });
      state.systemStatus.lan_enabled = payload.lan_enabled;
      state.status.lan_enabled = payload.lan_enabled;
      renderSystemStatus(state.systemStatus);
      settingsMessage(payload.lan_enabled ? "Other devices can now open PiStick." : "LAN access is now off.", true);
    } catch (error) {
      settingsMessage(error.message, false);
    } finally {
      ui.lanToggle.disabled = false;
    }
  }

  async function scanWifiNetworks() {
    ui.wifiScan.disabled = true;
    ui.wifiList.replaceChildren(emptyDeviceMessage("Scanning nearby Wi-Fi networks…"));
    settingsMessage("Wi-Fi scan can take several seconds.", false);
    try {
      var payload = await api("/api/system/wifi/scan", { method: "POST", body: {} });
      ui.wifiList.replaceChildren();
      var networks = payload.networks || [];
      if (!networks.length) ui.wifiList.append(emptyDeviceMessage("No Wi-Fi networks found."));
      networks.forEach(function (network) {
        var detail = network.signal + "% · " + network.security + (network.connected ? " · Connected" : "");
        ui.wifiList.append(deviceRow(network.ssid, detail, network.connected ? "" : "Connect", network.connected ? null : function () {
          connectWifiNetwork(network);
        }));
      });
      settingsMessage("Choose a Wi-Fi network to connect.", true);
    } catch (error) {
      ui.wifiList.replaceChildren();
      settingsMessage(error.message, false);
    } finally {
      ui.wifiScan.disabled = false;
    }
  }

  function connectWifiNetwork(network) {
    var connect = async function (password) {
      closeControllerKeyboard();
      settingsMessage("Connecting to " + network.ssid + "…", false);
      try {
        await api("/api/system/wifi/connect", {
          method: "POST",
          body: { ssid: network.ssid, password: password || "" }
        });
        settingsMessage("Connected to " + network.ssid + ".", true);
        await refreshSystemStatus();
      } catch (error) {
        settingsMessage(error.message + " Check the password and try again.", false);
      }
    };
    if (network.security === "Open") {
      connect("");
      return;
    }
    openControllerKeyboard(
      "Password for " + network.ssid,
      "",
      "Connect",
      connect,
      { password: true, trim: false }
    );
  }

  async function scanBluetoothControllers() {
    ui.bluetoothScan.disabled = true;
    ui.bluetoothList.replaceChildren(emptyDeviceMessage("Put the controller in pairing mode. Scanning…"));
    settingsMessage("Bluetooth scan can take about 10 seconds.", false);
    try {
      var payload = await api("/api/system/bluetooth/scan", { method: "POST", body: {} });
      renderBluetoothDevices(payload.devices || []);
      settingsMessage("Choose your controller, then select Pair.", true);
    } catch (error) {
      ui.bluetoothList.replaceChildren();
      settingsMessage(error.message, false);
    } finally {
      ui.bluetoothScan.disabled = false;
    }
  }

  async function pairBluetoothController(device) {
    settingsMessage("Pairing " + (device.name || device.address) + "…", false);
    try {
      await api("/api/system/bluetooth/pair", {
        method: "POST",
        body: { address: device.address }
      });
      settingsMessage("Controller paired and connected.", true);
      await refreshSystemStatus();
    } catch (error) {
      settingsMessage(error.message, false);
    }
  }

  function closeControllerKeyboard() {
    if (!ui.keyboardDialog || !ui.keyboardDialog.open) return;
    ui.keyboardDialog.close();
    state.keyboardSubmit = null;
    var returnFocus = state.keyboardReturnFocus;
    state.keyboardReturnFocus = null;
    if (returnFocus && document.contains(returnFocus)) {
      window.setTimeout(function () { returnFocus.focus(); }, 20);
    }
  }

  function appendKeyboardText(value) {
    var current = String(ui.keyboardValue.value || "");
    ui.keyboardValue.value = (current + String(value || "")).slice(0, 120);
  }

  function submitControllerKeyboard() {
    var rawValue = String(ui.keyboardValue.value || "");
    var value = state.keyboardTrim ? rawValue.trim() : rawValue;
    if (!value) {
      showToast("Enter at least one character.");
      return;
    }
    var callback = state.keyboardSubmit;
    state.keyboardSubmit = null;
    state.keyboardReturnFocus = null;
    ui.keyboardDialog.close();
    if (callback) Promise.resolve(callback(value)).catch(function (error) { showToast(error.message || error); });
  }

  function renderControllerKeyboardKeys() {
    ui.keyboardGrid.replaceChildren();
    var characters;
    if (state.keyboardLayout === "symbols") {
      characters = ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "-", "_", "=", "+", "[", "]", "{", "}", ";", ":", "'", "\"", ",", ".", "<", ">", "/", "?", "\\", "|", "`", "~"];
    } else {
      var alphabet = state.keyboardUpper ? "ABCDEFGHIJKLMNOPQRSTUVWXYZ" : "abcdefghijklmnopqrstuvwxyz";
      characters = (alphabet + "0123456789").split("");
    }
    characters.forEach(function (character) {
      ui.keyboardGrid.append(button(character, "keyboard-key", function () { appendKeyboardText(character); }));
    });
    if (state.keyboardLayout === "letters") {
      ui.keyboardGrid.append(button(state.keyboardUpper ? "lowercase" : "UPPERCASE", "keyboard-key keyboard-wide", function () {
        state.keyboardUpper = !state.keyboardUpper;
        renderControllerKeyboardKeys();
      }));
      ui.keyboardGrid.append(button("#+=", "keyboard-key keyboard-action", function () {
        state.keyboardLayout = "symbols";
        renderControllerKeyboardKeys();
      }));
    } else {
      ui.keyboardGrid.append(button("ABC", "keyboard-key keyboard-wide", function () {
        state.keyboardLayout = "letters";
        renderControllerKeyboardKeys();
      }));
    }
    ui.keyboardGrid.append(button("Space", "keyboard-key keyboard-wide", function () { appendKeyboardText(" "); }));
    ui.keyboardGrid.append(button("⌫ Backspace", "keyboard-key keyboard-wide", function () {
      ui.keyboardValue.value = ui.keyboardValue.value.slice(0, -1);
    }));
    ui.keyboardGrid.append(button("Clear", "keyboard-key keyboard-action", function () { ui.keyboardValue.value = ""; }));
    ui.keyboardGrid.append(button("Cancel", "keyboard-key keyboard-action", closeControllerKeyboard));
    ui.keyboardGrid.append(button(state.keyboardSubmitLabel, "keyboard-key keyboard-submit", submitControllerKeyboard));
  }

  function openControllerKeyboard(title, initialValue, submitLabel, onSubmit, options) {
    var keyboardOptions = options || {};
    state.keyboardReturnFocus = document.activeElement;
    state.keyboardSubmit = onSubmit;
    state.keyboardLayout = "letters";
    state.keyboardUpper = false;
    state.keyboardTrim = keyboardOptions.trim !== false;
    state.keyboardSubmitLabel = submitLabel || "Done";
    ui.keyboardTitle.textContent = title || "Enter text";
    ui.keyboardValue.type = keyboardOptions.password ? "password" : "text";
    ui.keyboardValue.value = String(initialValue || "").slice(0, 120);
    renderControllerKeyboardKeys();

    if (!ui.keyboardDialog.open) ui.keyboardDialog.showModal();
    window.setTimeout(function () {
      var first = ui.keyboardGrid.querySelector("button");
      if (first) first.focus();
    }, 30);
  }

  function openSearchKeyboard() {
    openControllerKeyboard("Search movies and TV shows", ui.searchInput.value, "Search", function (value) {
      ui.searchInput.value = value;
      return renderSearch(value);
    });
  }

  function visibleFocusable() {
    var root = document;
    if (ui.keyboardDialog.open) root = ui.keyboardDialog;
    else if (ui.settingsDialog.open) root = ui.settingsDialog;
    else if (ui.detailsDialog.open) root = ui.detailsDialog;
    else if (state.player) root = ui.playerOverlay;
    return Array.from(root.querySelectorAll("button:not([disabled]), input:not([disabled]):not([readonly]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")).filter(function (node) {
      var rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(node).visibility !== "hidden";
    });
  }

  function focusFirst() {
    var nodes = visibleFocusable();
    if (nodes.length) nodes[0].focus();
  }

  function moveFocus(direction) {
    var current = document.activeElement;
    if (current && current.classList && current.classList.contains("media-card") && (direction === "left" || direction === "right")) {
      var rail = current.closest(".media-row");
      if (rail) {
        var cards = Array.from(rail.querySelectorAll(".media-card"));
        var index = cards.indexOf(current);
        if (index >= 0 && cards.length) {
          var next = direction === "right" ? (index + 1) % cards.length : (index - 1 + cards.length) % cards.length;
          cards[next].focus();
          cards[next].scrollIntoView({ behavior: motionBehavior(), block: "nearest", inline: "center" });
          return;
        }
      }
    }
    var candidates = visibleFocusable();
    if (!candidates.length) return;
    if (!current || candidates.indexOf(current) < 0) {
      candidates[0].focus();
      return;
    }
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
    if (best) {
      best.focus();
      best.scrollIntoView({ behavior: motionBehavior(), block: "nearest", inline: "nearest" });
    }
  }

  function backAction() {
    if (ui.keyboardDialog.open) return closeControllerKeyboard();
    if (state.player) return closePlayer();
    if (ui.settingsDialog.open) return closeSettings();
    if (ui.detailsDialog.open) return closeDetails();
    if (state.currentView !== "home" && state.activeProfile) return renderHome();
    renderProfiles(false);
  }

  function playerDirectional(direction) {
    if (direction === "left") postPlayerCommand("seek-relative", { offsetSeconds: -10 });
    if (direction === "right") postPlayerCommand("seek-relative", { offsetSeconds: 10 });
  }

  function setupKeyboard() {
    document.addEventListener("keydown", function (event) {
      var target = event.target;
      var typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
      if (event.key === "Escape") {
        event.preventDefault();
        backAction();
        return;
      }
      if (ui.keyboardDialog.open) {
        if (event.key === "Enter") {
          event.preventDefault();
          submitControllerKeyboard();
        } else if (event.key === "Backspace") {
          event.preventDefault();
          ui.keyboardValue.value = ui.keyboardValue.value.slice(0, -1);
        } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
          event.preventDefault();
          appendKeyboardText(event.key);
        }
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
      gamepadEdge(gamepad, "a", gamepadPressed(gamepad, 0), function () {
        if (state.player) postPlayerCommand("toggle");
        else if (document.activeElement === ui.searchInput) openSearchKeyboard();
        else if (document.activeElement && document.activeElement.classList.contains("profile-name-input")) {
          var input = document.activeElement;
          openControllerKeyboard("Edit profile name", input.value, "Use name", function (value) { input.value = value; });
        }
        else if (document.activeElement && typeof document.activeElement.click === "function") document.activeElement.click();
        else focusFirst();
      });
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
    window.requestAnimationFrame(pollGamepads);
  }

  async function bootstrap() {
    ui.header = document.getElementById("site-header");
    ui.app = document.getElementById("app");
    ui.brandButton = document.getElementById("brand-button");
    ui.searchForm = document.getElementById("search-form");
    ui.searchInput = document.getElementById("search-input");
    ui.profileButton = document.getElementById("profile-button");
    ui.detailsDialog = document.getElementById("details-dialog");
    ui.detailsContent = document.getElementById("details-content");
    ui.detailsClose = document.getElementById("details-close");
    ui.settingsDialog = document.getElementById("settings-dialog");
    ui.settingsMessage = document.getElementById("settings-message");
    ui.settingsClose = document.getElementById("settings-close");
    ui.lanStatus = document.getElementById("lan-status");
    ui.lanToggle = document.getElementById("lan-toggle");
    ui.lanUrl = document.getElementById("lan-url");
    ui.wifiStatus = document.getElementById("wifi-status");
    ui.wifiScan = document.getElementById("wifi-scan");
    ui.wifiList = document.getElementById("wifi-list");
    ui.bluetoothStatus = document.getElementById("bluetooth-status");
    ui.bluetoothScan = document.getElementById("bluetooth-scan");
    ui.bluetoothList = document.getElementById("bluetooth-list");
    ui.wiredRefresh = document.getElementById("wired-refresh");
    ui.wiredList = document.getElementById("wired-list");
    ui.keyboardDialog = document.getElementById("keyboard-dialog");
    ui.keyboardTitle = document.getElementById("keyboard-title");
    ui.keyboardValue = document.getElementById("keyboard-value");
    ui.keyboardGrid = document.getElementById("keyboard-grid");
    ui.keyboardClose = document.getElementById("keyboard-close");
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
      renderSearch(ui.searchInput.value);
    });
    ui.profileButton.addEventListener("click", function () { renderProfiles(false); });
    ui.detailsClose.addEventListener("click", closeDetails);
    ui.settingsClose.addEventListener("click", closeSettings);
    ui.lanToggle.addEventListener("click", toggleLanAccess);
    ui.wifiScan.addEventListener("click", scanWifiNetworks);
    ui.bluetoothScan.addEventListener("click", scanBluetoothControllers);
    ui.wiredRefresh.addEventListener("click", refreshSystemStatus);
    ui.keyboardClose.addEventListener("click", closeControllerKeyboard);
    ui.playerClose.addEventListener("click", closePlayer);
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
    ui.keyboardDialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      closeControllerKeyboard();
    });
    window.addEventListener("message", function (event) {
      if (!state.player || state.player.trailer) return;
      if (event.origin !== "https://player.videasy.to" && event.origin !== "https://player.videasy.net") return;
      var progress = decodePlayerMessage(event.data);
      if (!progress || !(progress.position >= 0) || !(progress.duration > 0)) return;
      state.player.position = progress.position;
      state.player.duration = progress.duration;
      savePlayerProgress(false);
    });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) savePlayerProgress(true);
    });
    window.addEventListener("gamepadconnected", function (event) {
      showToast("Controller connected: " + (event.gamepad.id || "Gamepad"));
      focusFirst();
    });
    setupKeyboard();
    window.requestAnimationFrame(pollGamepads);

    loadingPage("Connecting to PiStick Server…", false);
    try {
      state.status = await api("/api/status");
      state.lowMemory = state.lowMemory || Boolean(state.status.low_memory);
      document.documentElement.classList.toggle("pi-zero-w", state.lowMemory);
      await refreshProfiles();
      if (state.activeProfile) await renderHome();
      else renderProfiles(false);
    } catch (error) {
      errorPage(error, bootstrap);
    }
  }

  document.addEventListener("DOMContentLoaded", bootstrap);
})();
