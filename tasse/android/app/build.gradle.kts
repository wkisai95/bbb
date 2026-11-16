plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val ciRun = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1
val linkAssets = layout.buildDirectory.dir("generated/linkAssets")
val copyLink = tasks.register<Copy>("copyLink") {
    from("../../../app/web/cifrato.js")
    into(linkAssets)
}
tasks.named("preBuild") { dependsOn(copyLink) }

val keystorePath: String? = System.getenv("ANDROID_KEYSTORE_PATH")

android {
    namespace = "com.wkisai.tasse"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.wkisai.tasse"
        minSdk = 26
        targetSdk = 35
        versionCode = ciRun
        versionName = "1.0.$ciRun"
    }

    // Interfaccia: tasse/web, più cifrato.js preso dall'app bbb (lo stesso file, così il formato resta uno solo)
    sourceSets {
        getByName("main") {
            assets.srcDirs("../../web", linkAssets)
        }
    }

    signingConfigs {
        create("release") {
            if (keystorePath != null) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystorePath != null) signingConfigs.getByName("release")
            else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
}
