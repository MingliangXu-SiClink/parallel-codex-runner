# Resource loading in the macOS app

Both MTFont and MathFont use the same packaged font bundle. An installed .app
loads SwiftMath_SwiftMath.bundle from Contents/Resources; it must not fall back
to SwiftPM's build-machine path. Command-line builds and unit tests retain the
standard Bundle.module behavior. Keep both font-loading paths consistent when
updating this dependency.
