# Parity check

The invariant: `site/index.html` and `service/container/render.py` are two
implementations of the same mathematics, and they must agree. The cipher is
bit-exact across both — FNV-1a key hash → mulberry32 keystream → base32 — and so
are sidereal time, the digit reduction and the great-circle distance. Ink
dispersion is allowed to differ, because the PRNGs differ. The cipher and the
arithmetic are not.

This checks it. Two commands, no build step, no test framework:

```bash
cd service/parity
node browser-side.js ../../site/index.html > /tmp/browser.json
python3 server-side.py ../container/render.py /tmp/browser.json
```

Expected:

```
92 comparisons across 6 cases
browser and server agree on every one
```

Any mismatch prints the case, the field, and both values, and exits non-zero.

`browser-side.js` extracts the shipped `<script>` block out of `index.html` and
runs it in a Node VM against a stub DOM — so it tests the code that actually
ships, not a transcription of it. If the page ever grows a top-level DOM call the
stub doesn't cover, that script will throw; the `sandbox` object at the top of
`browser-side.js` is where to add it.

The cases cover both moments and one, the southern and eastern hemispheres, a
quarter-hour UTC offset, a leap day, the antimeridian, zero-zero, and a message
carrying punctuation from outside the cipher's charset.

Requires the renderer's own dependencies (`numpy`, `pillow`) — see
`../container/requirements.txt`.
