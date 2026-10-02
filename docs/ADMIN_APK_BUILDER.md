# Admin APK Builder

The System Owner admin panel now has an **Android APK Builder**.

## Required Render environment variable

Set:

- `GITHUB_ACTIONS_TOKEN`: a GitHub token that can trigger Actions workflows and read workflow runs/artifacts.
- `GITHUB_ACTIONS_REPO`: optional; defaults to `ahsanibnewalid/python`.
- `GITHUB_APK_WORKFLOW`: optional; defaults to `build-android-apk.yml`.

Do not put the GitHub token in the React Native app, browser JavaScript, or repository source.

## Admin flow

1. Sign in as the System Owner.
2. Open **System Command Center** at `/admin/god`.
3. Use **Android APK Builder → Build installable APK**.
4. The server dispatches the existing GitHub Actions workflow.
5. The admin panel polls the workflow status.
6. After the artifact is ready, **Download APK** downloads the APK from the authenticated server endpoint.

The GitHub token is never sent to the browser.

## Token permissions

Use the smallest GitHub token permissions possible. It needs permission to:

- trigger the repository's Actions workflow;
- read the workflow run;
- read the generated workflow artifact.

The APK workflow remains the source of truth for the Android build.
