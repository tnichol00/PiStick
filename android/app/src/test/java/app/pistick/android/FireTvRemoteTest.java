package app.pistick.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.view.KeyEvent;

import org.junit.Test;

public final class FireTvRemoteTest {
    @Test
    public void mapsFireTvNavigationAndMediaButtons() {
        assertEquals(FireTvRemote.UP, FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_DPAD_UP));
        assertEquals(FireTvRemote.SELECT, FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_DPAD_CENTER));
        assertEquals(FireTvRemote.BACK, FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_BACK));
        assertEquals(FireTvRemote.PLAY_PAUSE,
                FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE));
        assertEquals(FireTvRemote.REWIND,
                FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_MEDIA_REWIND));
        assertEquals(FireTvRemote.FAST_FORWARD,
                FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_MEDIA_FAST_FORWARD));
        assertEquals(FireTvRemote.MENU, FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_MENU));
        assertNull(FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_BUTTON_A));
        assertNull(FireTvRemote.actionForKeyCode(KeyEvent.KEYCODE_VOLUME_UP));
    }

    @Test
    public void onlyDirectionalActionsRepeat() {
        assertTrue(FireTvRemote.isRepeatable(FireTvRemote.LEFT));
        assertTrue(FireTvRemote.isRepeatable(FireTvRemote.DOWN));
        assertFalse(FireTvRemote.isRepeatable(FireTvRemote.SELECT));
        assertFalse(FireTvRemote.isRepeatable(FireTvRemote.PLAY_PAUSE));
    }

    @Test
    public void convertsPlayerActionsBackToNativeKeys() {
        assertEquals(KeyEvent.KEYCODE_DPAD_RIGHT,
                FireTvRemote.keyCodeForAction(FireTvRemote.RIGHT));
        assertEquals(KeyEvent.KEYCODE_DPAD_CENTER,
                FireTvRemote.keyCodeForAction(FireTvRemote.SELECT));
        assertEquals(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                FireTvRemote.keyCodeForAction(FireTvRemote.PLAY_PAUSE));
        assertEquals(KeyEvent.KEYCODE_UNKNOWN,
                FireTvRemote.keyCodeForAction("invalid"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                FireTvRemote.playerKeyCodeForAction(FireTvRemote.SELECT));
        assertEquals(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                FireTvRemote.playerKeyCodeForAction(FireTvRemote.PLAY_PAUSE));
    }

    @Test
    public void convertsExactSeekDurationsToTenSecondPlayerSteps() {
        assertEquals(1, FireTvRemote.seekPressCount(10));
        assertEquals(1, FireTvRemote.seekPressCount(-10));
        assertEquals(30, FireTvRemote.seekPressCount(5 * 60));
        assertEquals(30, FireTvRemote.seekPressCount(-5 * 60));
        assertEquals(0, FireTvRemote.seekPressCount(0));
    }
}
