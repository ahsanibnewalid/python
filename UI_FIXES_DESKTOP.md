# Desktop UI Fixes

## Home / Feed
- Desktop browsers (1051px+) now use a fixed application viewport below the top navigation.
- The main newsfeed is an independent scroll container; the whole browser page no longer scrolls when reading the feed.
- Left and right sidebars remain available while the feed scrolls.
- Mobile/tablet layouts retain normal document scrolling.
- Scroll containment prevents wheel/touch scrolling from escaping the feed container.

## Facebook-style Inbox
- The Messages navigation item on the desktop home page opens a Facebook-style inbox popover.
- Recent conversations, unread counts, avatars and last-message previews are shown.
- The popover has its own scroll area and an Open Messages footer.
- Clicking outside or pressing Escape closes it.
- The full Messages page remains available through See all / Open Messages.

This update intentionally leaves the working backend/database schema unchanged.
