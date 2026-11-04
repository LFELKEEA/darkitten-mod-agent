package net.fabricmc.loom.nativeplatform;

public class LoomNativePlatformException extends RuntimeException {
    public LoomNativePlatformException(String message) {
        super(message);
    }

    public LoomNativePlatformException(String message, Throwable cause) {
        super(message, cause);
    }
}
