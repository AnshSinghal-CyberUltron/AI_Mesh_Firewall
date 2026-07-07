# Legacy demo tree (reference only)

Production builds and active development now use the canonical demo app at:

**[`demo/zeroshield-openai-demo`](../../demo/zeroshield-openai-demo/)**

The ECR image `ai-mesh-demo` is built from that directory via
`infra/scripts/build-push-images.sh`.

This `examples/` copy is retained for historical SDK scenario snippets and
Playwright smoke tests. Do not ship production changes here — backport to
`demo/zeroshield-openai-demo` instead.
