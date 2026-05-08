# patchfeld website

Marketing site for [patchfeld](../README.md), hosted at
[patchfeld.com](https://patchfeld.com).

Astro + Tailwind, static-first, single landing page. No backend.

## Stack

- [Astro 5](https://astro.build) — static site generator with component islands
- [Tailwind CSS 4](https://tailwindcss.com) — via the official Vite plugin
- TypeScript (strict)
- No animation library — plain CSS + a tiny `IntersectionObserver` for fade-in

Astro was chosen because the site is content-first and ships almost zero
JavaScript by default; the only client-side JS is the copy-to-clipboard
button and the install-tab switcher (a few dozen lines, inlined per
component). If we ever need MDX or a docs subsite, Astro accommodates
both without a rewrite.

## Prerequisites

- Node.js 20.x or 22.x (24.x works in dev — official Astro support is 22 LTS)
- npm 10+

## Local development

```bash
cd website
npm install
npm run dev
```

The dev server runs on <http://localhost:4321>.

## Build

```bash
cd website
npm run build      # outputs to website/dist/
npm run preview    # serves the built site
```

The build is fully static: `dist/` contains HTML, CSS, optimized images,
and a small bit of JS for the interactive bits.

## Project layout

```
website/
  package.json
  astro.config.mjs
  tsconfig.json
  src/
    pages/
      index.astro              # the whole site is one page for v1
    layouts/
      Base.astro                # html shell, fonts, meta, reveal observer
    components/
      Nav.astro
      Hero.astro
      Features.astro
      Screenshot.astro
      Examples.astro
      Permissions.astro
      Widgets.astro
      Install.astro
      Footer.astro
    styles/
      global.css                # Tailwind import + design tokens + base
    assets/
      screenshot.png            # copied from ../docs/images/screenshot.png
  public/
    favicon.svg
```

## Updating the screenshot

If the screenshot in the project root (`../docs/images/screenshot.png`)
is regenerated, copy it back over:

```bash
cp ../docs/images/screenshot.png src/assets/screenshot.png
```

`src/assets/` is processed by Astro's image pipeline (compression,
responsive sizing, lazy loading), which is why we import it via
`astro:assets` in `Screenshot.astro`.

## Deploying

The site is static — any host that serves `dist/` works.

### Cloudflare Pages (recommended)

1. Create a new Pages project pointed at this repo.
2. Set the **build command** to `npm run build`.
3. Set the **build output directory** to `dist`.
4. Set the **root directory** to `website`.
5. Add the custom domain `patchfeld.com` once DNS is set up.

Cloudflare Pages auto-deploys on push and handles HTTPS for free.

### Alternatives

- **Vercel:** import the repo, set the root directory to `website`, framework
  preset "Astro". Vercel auto-detects build command + output.
- **Netlify:** set base directory `website`, build command `npm run build`,
  publish directory `website/dist`.
- **GitHub Pages:** publish `dist/` from a workflow. There's no built-in
  Astro action; use [`actions/deploy-pages`](https://github.com/actions/deploy-pages)
  with `actions/upload-pages-artifact` pointed at `website/dist`.

## Updating copy

The site's copy is consistent with the project README's voice: direct,
technical, slightly playful. When you add or rewrite a section, read the
project root README first and match its register. Don't slip into
generic SaaS phrasing.

GitHub URL: the repo is `jimmymills/patchfeld`. The old `patchbai` URL
still redirects via GitHub's rename redirect, but everything in the site
points at the canonical name.

## License

MIT — same as the project itself.
