---
id: industry_norms_005_mobile
type: industry_norm
title: Mobile Engineering — Resume Conventions
roles: [mobile]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Mobile Engineering — Resume Conventions

Mobile splits into native iOS, native Android, and cross-platform (React Native, Flutter, KMM). Wikipedia's mobile-development overview (2026): iOS uses Swift / Objective-C via Xcode; Android uses Kotlin / Java via Android Studio. App Store and Play Store review gates shape the work cycle.

**Tech-stack categories:**

- **iOS native:** Swift (default), Objective-C (legacy), SwiftUI, UIKit, Combine, Core Data, XCTest, TestFlight, App Store Connect.
- **Android native:** Kotlin (default), Java (legacy), Jetpack Compose, Room, Hilt, Coroutines + Flow, JUnit + Espresso, Play Console.
- **Cross-platform:** React Native (Expo or bare), Flutter (Dart), Kotlin Multiplatform (KMM), .NET MAUI.
- **Backend-for-mobile:** Firebase (Firestore, Auth, Functions, Crashlytics), AWS Amplify, Supabase, custom REST / GraphQL.
- **Build & CI:** fastlane, Bitrise, Codemagic, GitHub Actions, Xcode Cloud.
- **Analytics:** Firebase Analytics, Mixpanel, Amplitude, Crashlytics, Sentry, Datadog Mobile RUM.

**Performance and quality metrics:**

- App size (MB), cold/warm start time, frames per second (60fps target), ANR rate (Android), hang rate (iOS).
- Crash-free sessions / users (%) via Crashlytics or Sentry.
- Battery impact, network-data per session.
- App Store / Play Store rating + review count, download count.

**Strong bullet examples for mobile:**

- "Cut iOS cold-start from 3.4s to 1.1s on iPhone 12 by deferring 8 SDK inits and lazy-loading the home feed; rating moved from 3.9 to 4.5 over Q3."
- "Migrated Android app from XML Views to Jetpack Compose across 24 screens in 4 months; APK size dropped 18%; product-list frame drops fell from 12% to under 1%."
- "Owned the React Native checkout used by 180K MAU; moved totals-recalc into a JSI native module; p95 input-to-render dropped from 320ms to 90ms."
- "Raised crash-free sessions from 99.1% to 99.92% in Q2 via null-safety guards in legacy Objective-C image cache and JS strict-mode."
- "Shipped 14 releases across iOS + Android in 2024 (bi-weekly); 4.6 avg rating across 50K reviews; no production-blocking crash escapes."

**Mobile anti-patterns:**

- Both iOS and Android as primary without depth. Most production engineers specialize.
- "Built mobile apps" with no names, store links, or downloads.
- Cordova / Ionic / PhoneGap in 2026 stack — outdates the candidate.
- Claiming Flutter or React Native without a named shipped app.

**Public app links.** Include store links in contact or Skills. Skip removed or sub-3-star apps.

**Store-policy navigation.** Mid+ engineers need one bullet on App Store / Play Store policy work or platform deprecations (scoped storage, App Tracking Transparency).

## Concrete rule for SmartCV

For mobile roles, surface the platform-specific stack (iOS native, Android native, OR cross-platform — usually one primary, with a secondary). Quantify bullets with cold-start time, crash-free %, frame rate, app size, and store rating. Always include at least one bullet referencing a public app (with store link) or a named enterprise app with user-base scale. For mid+ candidates, generate one bullet demonstrating navigation of a platform constraint or store-policy issue.

---
sources:
  - https://en.wikipedia.org/wiki/Mobile_app_development  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resumes-cover-letters/software-engineer-resume  (accessed 2026-05-12)
