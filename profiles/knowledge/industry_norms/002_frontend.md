---
id: industry_norms_002_frontend
type: industry_norm
title: Frontend Engineering — Resume Conventions
roles: [frontend, fullstack]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Frontend Engineering — Resume Conventions

Frontend resumes are weighted heavily on framework specifics, performance metrics, and demonstrable production work. Unlike backend roles where the work is invisible to evaluators, frontend work has a public artifact (the live app), so links to deployed projects carry significant weight.

**Tech-stack categories:**

- **Languages:** HTML, CSS (SCSS/Sass), JavaScript, TypeScript.
- **Framework:** React, Vue, Angular, Svelte; meta-frameworks Next.js / Nuxt / SvelteKit / Remix.
- **State:** Redux, Zustand, Jotai, Pinia, NgRx, React Query, SWR.
- **Styling:** Tailwind, CSS Modules, styled-components, MUI, Chakra, Mantine, Ant Design.
- **Build:** Vite, Webpack, Rollup, esbuild, Turbopack, Bun.
- **Testing:** Jest, Vitest, React Testing Library, Cypress, Playwright, Storybook.
- **A11y:** axe-core, Lighthouse, WCAG (A/AA/AAA), NVDA/VoiceOver testing.

**Performance metrics — Core Web Vitals (web.dev):**
- **LCP:** ≤ 2.5s for "good".
- **INP:** ≤ 200ms for "good".
- **CLS:** ≤ 0.1 for "good".
All at 75th percentile, segmented mobile/desktop.

Other metrics: bundle size (KB), Time-to-Interactive, Lighthouse score, 99th-pp interaction latency, image-optimization savings, axe-violation count.

**Strong bullet examples for frontend:**

- "Cut LCP on the marketing homepage from 4.1s to 1.8s by deferring third-party scripts, switching hero to AVIF with `<picture>` fallback, and inlining critical CSS."
- "Built a TypeScript + Tailwind component library as an internal npm package; adopted by 4 product teams; replaced 12 duplicated implementations across the monorepo."
- "Migrated checkout from Redux Saga to React Query; cut state-management code by 38% and eliminated 6 categories of stale-cache bugs."
- "Brought 14 marketing pages from WCAG A to WCAG AA; axe-core violations dropped from 78 to 0."
- "Reduced JS bundle from 480KB to 180KB by code-splitting the dashboard route, removing moment.js in favor of date-fns, and switching to dynamic imports for the chart library."

**Frontend anti-patterns:**

- Listing every framework. If recent 3 years were React, downgrade Angular to a single Skills mention.
- "Built responsive websites" with no metric. Replace with breakpoint count, device-test matrix, or LCP/CLS.
- Claiming "expert in CSS" without examples. Show through animation, layout, design-token bullets.
- Mentioning jQuery on a 2026 resume unless legacy maintenance is the role — outdates the candidate.

**Portfolio link required.** Frontend resumes without a live portfolio link are widely viewed as incomplete by recruiters for this role family. Portfolio should load <2.5s on mobile, show one production-quality interactive demo, and not be a starter clone.

## Concrete rule for SmartCV

For frontend roles, surface the framework-specific stack in Skills (Language, Framework, State, Styling, Build, Testing, A11y as separate sub-groups). Quantify performance bullets using Core Web Vitals (LCP/INP/CLS) and bundle size. Always include at least one bullet demonstrating accessibility or performance work, even if other bullets focus on features. Require a portfolio link in the contact block. Do not list jQuery in a candidate's main stack unless the JD explicitly involves a legacy jQuery codebase.

---
sources:
  - https://web.dev/articles/vitals  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Front-end_web_development  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
