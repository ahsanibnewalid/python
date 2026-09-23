# Story video playback fix

Based on `UniversityConnect_FINAL_MULTIUSER_FIXED(2).zip`.

## Fixed
The story viewer previously used a fixed 5-second timer for every story. That caused uploaded videos longer than 5 seconds to be closed/advanced before the video finished.

The viewer now:
- lets story videos play for their full natural duration;
- advances only on the video's `ended` event;
- uses the actual video duration for the progress bar;
- keeps the existing 5-second duration for image stories;
- retains video controls, autoplay, inline playback, and preloading.

No other project behavior was intentionally changed.
