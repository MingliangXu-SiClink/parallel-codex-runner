pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        providers.environmentVariable("PCR_ANDROID_MAVEN_MIRROR")
            .orNull
            ?.takeIf(String::isNotBlank)
            ?.let { mirror ->
                maven {
                    url = uri(mirror)
                    isAllowInsecureProtocol = mirror.startsWith("http://")
                }
            }
        google()
        mavenCentral()
    }
}

rootProject.name = "ParallelCodexAndroid"
include(":app")
