package app.pistick.android;

import android.view.KeyEvent;

final class FireTvRemote {
    static final int SEEK_STEP_SECONDS = 10;
    static final int LONG_SEEK_SECONDS = 5 * 60;
    static final String UP = "up";
    static final String DOWN = "down";
    static final String LEFT = "left";
    static final String RIGHT = "right";
    static final String SELECT = "select";
    static final String BACK = "back";
    static final String PLAY_PAUSE = "play-pause";
    static final String PLAY = "play";
    static final String PAUSE = "pause";
    static final String REWIND = "rewind";
    static final String FAST_FORWARD = "fast-forward";
    static final String MENU = "menu";
    static final String STOP = "stop";

    private FireTvRemote() {}

    static String actionForKeyCode(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_UP:
                return UP;
            case KeyEvent.KEYCODE_DPAD_DOWN:
                return DOWN;
            case KeyEvent.KEYCODE_DPAD_LEFT:
                return LEFT;
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                return RIGHT;
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_NUMPAD_ENTER:
                return SELECT;
            case KeyEvent.KEYCODE_BACK:
            case KeyEvent.KEYCODE_BUTTON_B:
                return BACK;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
                return PLAY_PAUSE;
            case KeyEvent.KEYCODE_MEDIA_PLAY:
                return PLAY;
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                return PAUSE;
            case KeyEvent.KEYCODE_MEDIA_REWIND:
                return REWIND;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
                return FAST_FORWARD;
            case KeyEvent.KEYCODE_MENU:
                return MENU;
            case KeyEvent.KEYCODE_MEDIA_STOP:
                return STOP;
            default:
                return null;
        }
    }

    static boolean isRepeatable(String action) {
        return UP.equals(action) || DOWN.equals(action) || LEFT.equals(action) || RIGHT.equals(action);
    }

    static int seekPressCount(int offsetSeconds) {
        int seconds = Math.abs(offsetSeconds);
        if (seconds == 0) return 0;
        return Math.max(1, (seconds + SEEK_STEP_SECONDS - 1) / SEEK_STEP_SECONDS);
    }

    static int keyCodeForAction(String action) {
        if (UP.equals(action)) return KeyEvent.KEYCODE_DPAD_UP;
        if (DOWN.equals(action)) return KeyEvent.KEYCODE_DPAD_DOWN;
        if (LEFT.equals(action)) return KeyEvent.KEYCODE_DPAD_LEFT;
        if (RIGHT.equals(action)) return KeyEvent.KEYCODE_DPAD_RIGHT;
        if (SELECT.equals(action)) return KeyEvent.KEYCODE_DPAD_CENTER;
        if (PLAY_PAUSE.equals(action)) return KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE;
        if (PLAY.equals(action)) return KeyEvent.KEYCODE_MEDIA_PLAY;
        if (PAUSE.equals(action)) return KeyEvent.KEYCODE_MEDIA_PAUSE;
        if (REWIND.equals(action)) return KeyEvent.KEYCODE_MEDIA_REWIND;
        if (FAST_FORWARD.equals(action)) return KeyEvent.KEYCODE_MEDIA_FAST_FORWARD;
        if (MENU.equals(action)) return KeyEvent.KEYCODE_MENU;
        if (STOP.equals(action)) return KeyEvent.KEYCODE_MEDIA_STOP;
        return KeyEvent.KEYCODE_UNKNOWN;
    }

    static int playerKeyCodeForAction(String action) {
        if (SELECT.equals(action)) return KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE;
        return keyCodeForAction(action);
    }
}
