# Contributing

The piece has one rule: **every mark must be downstream of an input.**

If you add a visual element, it has to be driven by something computed from the
moments — a digit sum, an angle, a distance. Decoration that isn't derived from
the inputs doesn't belong, however good it looks.

Two further conventions:

- **Keep the three registers separate.** Exact arithmetic, real cipher, and
  interpretive convention are labelled as such in the UI. Don't let the third
  borrow the authority of the first two.
- **Both renderers must agree.** `site/index.html` and `service/container/render.py`
  implement the same maths. If you change one, change the other, and check that
  the cipher still round-trips identically.

Open an issue before large changes so we can talk about shape.
