# UniversityConnect Android app

This native Expo/React Native client consumes the existing Flask application through /api/v1 and shares the same users, feed, Posts, Reels, Stories, direct messages, notifications and workspace data.

Set EXPO_PUBLIC_API_URL to the deployed Flask base URL before starting. For a physical Android device, use the Render URL or a LAN-reachable development server.

The project uses Expo Router for file-based navigation, Expo SDK 57, expo-image-picker for photo/video selection, expo-video for playback, and expo-secure-store for local session tokens. Expo SDK 57 currently targets Android 7+ and requires Node 22.13.x or newer according to the official Expo SDK reference.
