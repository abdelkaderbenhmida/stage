# web/static/platform

The platform-ops (operator) console — the half of the SPA gated on the global operator
role rather than per-team RBAC. Shares a page and DOM with the main control-plane SPA
(`web/static/app.js`), so this wraps itself in an IIFE and exports exactly one global,
`window.PlatformConsole`, instead of leaking globals that would collide with the SPA's
own `api`, `esc`, `toast`, `$`, etc.

- `app.js` — the console's state machine and views (`topology`, `apps`, `config`); talks
  to the `platform.py` router. `state.configTab` and similar literals are regexed by
  `controlplane/tests/test_ui.py` — renaming console tabs or restructuring those
  literals breaks that test. `MERGED_VIEWS` remaps old `overview`/`services` routes onto
  the current `topology`/`apps` views so existing links still resolve.
- `style.css` — styling for this console, scoped to `#platform-root` (shares many class
  names with `../style.css`'s `#cp-root` scope, so the prefix is load-bearing, not
  cosmetic).
