import java.util.Properties
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import javax.imageio.ImageIO
import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.isFile) file.inputStream().use(::load)
}

val keystoreProperties = Properties().apply {
    val file = rootProject.file("keystore.properties")
    if (file.isFile) file.inputStream().use(::load)
}

fun configured(name: String): String =
    providers.gradleProperty(name).orNull
        ?: localProperties.getProperty(name)
        ?: ""

fun quoted(value: String): String =
    "\"${value.replace("\\", "\\\\").replace("\"", "\\\"")}\""

abstract class GenerateBrandResources : DefaultTask() {
    @get:InputFile
    abstract val brandSource: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun generate() {
        val drawableDirectory = outputDirectory.get().asFile
            .resolve("drawable-nodpi")
            .toPath()
        Files.createDirectories(drawableDirectory)
        Files.copy(
            brandSource.get().asFile.toPath(),
            drawableDirectory.resolve("parallel_codex_brand.png"),
            StandardCopyOption.REPLACE_EXISTING,
        )
        val source = ImageIO.read(brandSource.get().asFile)
            ?: error("Unable to decode Parallel Codex brand image")
        val uiSize = 384
        val uiImage = BufferedImage(uiSize, uiSize, BufferedImage.TYPE_INT_ARGB)
        val graphics = uiImage.createGraphics()
        try {
            graphics.setRenderingHint(
                RenderingHints.KEY_INTERPOLATION,
                RenderingHints.VALUE_INTERPOLATION_BICUBIC,
            )
            graphics.setRenderingHint(
                RenderingHints.KEY_RENDERING,
                RenderingHints.VALUE_RENDER_QUALITY,
            )
            graphics.drawImage(source, 0, 0, uiSize, uiSize, null)
        } finally {
            graphics.dispose()
        }
        ImageIO.write(
            uiImage,
            "png",
            drawableDirectory.resolve("parallel_codex_brand_ui.png").toFile(),
        )
    }
}

val generateBrandResources = tasks.register<GenerateBrandResources>("generateBrandResources") {
    brandSource.set(
        rootProject.layout.projectDirectory.file(
            "../branding/parallel-codex-icon-1024.png"
        )
    )
    outputDirectory.set(layout.buildDirectory.dir("generated/pcr-brand/main"))
}

android {
    namespace = "com.parallelcodexrunner.remote"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.parallelcodexrunner.remote"
        minSdk = 28
        targetSdk = 37
        versionCode = 270
        versionName = "0.1.269"

        buildConfigField("String", "DEFAULT_RELAY_URL", quoted(configured("PCR_RELAY_URL")))
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    signingConfigs {
        val required = listOf("storeFile", "storePassword", "keyAlias", "keyPassword")
        if (required.all { !keystoreProperties.getProperty(it).isNullOrBlank() }) {
            create("release") {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }
}

androidComponents {
    onVariants(selector().all()) { variant ->
        variant.sources.res?.addGeneratedSourceDirectory(
            generateBrandResources,
            GenerateBrandResources::outputDirectory,
        )
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.06.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.10.0")
    implementation("androidx.work:work-runtime-ktx:2.11.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("com.mikepenz:multiplatform-markdown-renderer-m3:0.41.0")
    implementation("io.noties.markwon:core:4.6.2")
    implementation("io.noties.markwon:ext-latex:4.6.2")
    implementation("io.noties.markwon:ext-tables:4.6.2")
    implementation("io.noties.markwon:inline-parser:4.6.2")
    implementation("com.squareup.okhttp3:okhttp:5.3.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("org.bouncycastle:bcprov-jdk18on:1.85")

    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
