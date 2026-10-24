# UI theme

The Streamlit theme is defined in `.streamlit/config.toml`. Keep shared visual tokens there
instead of adding page-specific CSS. It uses a Carbon-inspired light palette, IBM Plex Sans for
interface text, IBM Plex Mono for code and technical values, square controls, and visible widget
and table borders. Streamlit's built-in sans-serif and monospace fonts are configured as
fallbacks if the hosted font service is unavailable.

`ui_shared.py` owns presentation helpers shared across pages, including semantic result colors
and the validated AI explanation panel. Use those helpers when adding a new status or showing an
AI response so its uncertainty, evidence, and review label remain consistent.

The IBM Plex font stylesheets are loaded from Google Fonts, which means browsers need network
access to display IBM Plex. The local fallback fonts keep the interface readable offline.
EDIResolve AI retains its own product identity; the visual treatment is inspired by Carbon and
does not indicate IBM endorsement.
