plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.gms.google-services")
}

fun normalizedAgentVersion(raw: String?): String {
    val trimmed = raw?.trim().orEmpty()
    if (trimmed.isEmpty()) {
        return "v0.1.0"
    }
    return if (trimmed.startsWith("v")) trimmed else "v$trimmed"
}

fun versionNameFromTag(tag: String): String = tag.removePrefix("v")

fun versionCodeFromTag(tag: String): Int {
    val stripped = tag.removePrefix("v")
    val parts = stripped.split(Regex("[.\\-_+]")).filter { it.isNotEmpty() }
    val major = parts.getOrNull(0)?.toIntOrNull() ?: 0
    val minor = parts.getOrNull(1)?.toIntOrNull() ?: 0
    val patch = parts.getOrNull(2)?.toIntOrNull() ?: 0
    return major * 1_000_000 + minor * 1_000 + patch
}

val releaseAgentVersion = normalizedAgentVersion(System.getenv("GUARDIAN_AGENT_VERSION"))

fun gradleStringLiteral(value: String): String {
    return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}

val sentryDsn = System.getenv("SENTRY_DSN").orEmpty().trim()

android {
    namespace = "com.guardian.agent"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.guardian.agent"
        minSdk = 24
        targetSdk = 35
        versionCode = versionCodeFromTag(releaseAgentVersion)
        versionName = versionNameFromTag(releaseAgentVersion)
        buildConfigField("String", "DEFAULT_AGENT_VERSION", "\"$releaseAgentVersion\"")
        buildConfigField("String", "SENTRY_DSN", gradleStringLiteral(sentryDsn))
        // SentryInitProvider reads manifest meta-data, not BuildConfig; kept for tooling/docs.
        manifestPlaceholders["sentryDsn"] = sentryDsn
    }

    signingConfigs {
        val keystorePath = (System.getenv("ANDROID_KEYSTORE_PATH")
            ?: project.findProperty("android.keystore.path")?.toString()).orEmpty()
        if (keystorePath.isNotBlank() && file(keystorePath).exists()) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = (System.getenv("ANDROID_KEYSTORE_PASSWORD")
                    ?: project.findProperty("android.keystore.password")?.toString()).orEmpty()
                keyAlias = (System.getenv("ANDROID_KEY_ALIAS")
                    ?: project.findProperty("android.key.alias")?.toString()).orEmpty()
                keyPassword = (System.getenv("ANDROID_KEY_PASSWORD")
                    ?: project.findProperty("android.key.password")?.toString()).orEmpty()
            }
        }
    }

    buildTypes {
        debug {
            buildConfigField("String", "DEFAULT_AGENT_VERSION", "\"v0.0.0-dev\"")
        }
        release {
            isMinifyEnabled = false
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.0.4")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("androidx.fragment:fragment-ktx:1.8.5")
    implementation("androidx.viewpager2:viewpager2:1.1.0")
    implementation("androidx.camera:camera-camera2:1.4.1")
    implementation("androidx.camera:camera-lifecycle:1.4.1")
    implementation("androidx.camera:camera-view:1.4.1")
    implementation("com.google.mlkit:barcode-scanning:17.3.0")
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-messaging-ktx")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.9.0")
    implementation("net.java.dev.jna:jna:5.14.0@aar")
    implementation("io.sentry:sentry-android:8.1.0")
    testImplementation("junit:junit:4.13.2")
}
