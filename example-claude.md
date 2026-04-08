## Project Overview
ShopFront is a B2C e-commerce web app for fashion and lifestyle products.
Primary users: shoppers aged 20–35 on mobile devices.

Optimize for:
- fast page load (Core Web Vitals)
- smooth checkout flow (reduce cart abandonment)
- mobile-first responsive design

Avoid over-engineering. Prefer clarity over cleverness.

## Tech Stack
- Next.js 15 with App Router
- TypeScript (strict mode)
- Tailwind CSS + shadcn/ui
- Zustand for cart/session state
- Supabase (auth + product database)
- Stripe for payment processing
- Vitest for unit tests

Do not introduce:
- Redux or MobX
- styled-components or Emotion
- Material UI or Ant Design
unless explicitly requested.

## Architecture
- app/                   → routes and server components
- components/ui/         → reusable design-system primitives
- components/product/    → product-specific UI (cards, gallery, filters)
- features/cart/         → cart logic, hooks, and local state
- features/checkout/     → checkout flow and Stripe integration
- lib/                   → shared utilities and API helpers
- types/                 → shared TypeScript interfaces

Rules:
- Keep API calls in lib/ or server actions only
- Never put side effects inside presentational components
- New feature? Create under features/{feature-name}/
- Prefer editing existing components over creating near-duplicates

## Coding Conventions
- TypeScript strict mode — avoid `any` at all times
- Named exports only (except Next.js route files)
- async/await over chained .then()
- Keep components under 200 lines unless justified
- Descriptive variable names — no abbreviations (qty → quantity)
- No dead code, no commented-out blocks
- Add comments only when intent is non-obvious
- Extract repeated logic into hooks under features/{name}/hooks/

## UI & Design System
- Use shadcn/ui primitives as default foundation
- 8px spacing rhythm throughout (p-2, p-4, p-8)
- Tailwind utilities only — no custom CSS files
- Product images: always use next/image with proper aspect ratios
- Every interactive element needs: hover, focus, and disabled states
- Forms must be scannable and mobile-friendly
- Meet WCAG 2.1 AA for contrast and keyboard navigation
- CTA buttons: solid primary only — no ghost buttons for main actions

## Content & Copy
- Concise and direct — no hype, no filler phrases
- Product headlines: benefit-first, not feature-first
- Error messages: tell users what to do, not just what went wrong
- CTA labels: action verbs ("Add to Cart", "Continue to Payment")
- Avoid: "World-class", "Cutting-edge", "Seamless experience"
- Price display: always show currency symbol, use comma separator (฿1,290)

## Testing & Quality
Before marking any task complete:
- run typecheck (bun typecheck)
- run lint (bun lint)
- run relevant tests (bun test)

Rules:
- Unit tests required for: cart calculations, discount logic,
  form validation, price formatting
- No heavy test scaffolding for simple presentational components
- For all data-driven UI: verify empty, loading, and error states
- Checkout flow changes require E2E test coverage

## File Placement
- New product UI components → components/product/
- Reusable UI primitives → components/ui/
- Cart/wishlist logic → features/cart/ or features/wishlist/
- Shared helpers → lib/
- API route handlers → app/api/{resource}/route.ts

Rules:
- Do not create a new abstraction for one-off usage
- Edit existing component before creating near-duplicate
- Component filename must match exported name (ProductCard.tsx → ProductCard)

## Safety Rules
- Do not rename or restructure public API routes (/api)
- Do not modify Stripe webhook handler without explicit request
- Do not change Supabase schema without flagging it clearly first
- Do not modify auth flow (login, register, session handling)
- Preserve backward compatibility for all shared components
- Flag major architectural changes before implementing 
— describe the change and wait for approval

## Commands
- Install:       bun install
- Dev:           bun dev          (runs on localhost:3000)
- Build:         bun build
- Lint:          bun lint
- Typecheck:     bun typecheck
- Test:          bun test
- Test (watch):  bun test:watch
- DB migrate:    bun db:migrate
- DB seed:       bun db:seed      (dev environment only)
- Stripe CLI:    stripe listen --forward-to localhost:3000/api/webhooks/stripe

## Security Rules
- Never commit .env, .env.local, or any file containing secrets
- Never hardcode API keys, tokens, or passwords in source code
- Never log sensitive data:
  - no console.log(user.password)
  - no logging full request bodies containing payment info
  - no logging Stripe webhook payloads in full
- Stripe keys:
  - NEXT_PUBLIC_ prefix → publishable key only (client-safe)
  - Secret key → server-side exclusively, never import in client components
- Supabase service role key: server-side only, never expose to client
- All user input must be validated server-side before hitting database
- Use Supabase RLS (Row Level Security) - never bypass with service role
  unless explicitly required and justified in a comment
- .env.example is the only env file allowed in version control,
  must contain placeholder values only (no real secrets)