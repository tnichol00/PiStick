package app.pistick.android;

final class PiStickException extends Exception {
    PiStickException(String message) {
        super(message);
    }

    PiStickException(String message, Throwable cause) {
        super(message, cause);
    }
}
