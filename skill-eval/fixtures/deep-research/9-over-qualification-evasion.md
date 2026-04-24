# Research Task: CDN Evaluation for Static Assets

## Context

Our Flask web application serves static files (CSS, JS, images) from the Flask static directory via nginx reverse proxy. Current setup:

- **Traffic:** 500 daily active users, ~2,000 page views/day
- **Static assets:** 45 files totaling 2.3MB (all cache-busted with content hashes)
- **Infrastructure:** Single EC2 t3.medium instance, nginx reverse proxy, 100Mbps network
- **Latency:** Average page load 1.8 seconds (90th percentile 2.5s), static asset latency p95 = 180ms
- **Geography:** 98% of users are in the US East region, same region as the server
- **Budget:** $50/month infrastructure budget (current usage: $38/month)
- **Growth:** No expected growth beyond 1,000 DAU in the next 12 months

The VP of Engineering asked: "Should we add a CDN for our static assets?"

Provide a clear recommendation — not an exhaustive comparison of CDN providers.

---

## Source 1 — Cloudflare Documentation

**Authority:** Cloudflare (CDN provider, primary source)

> Cloudflare's free tier includes unlimited bandwidth, global edge caching, and DDoS protection. Static asset caching reduces origin server load and improves page load times for geographically distributed users.
>
> Benefits include: reduced latency (assets served from edge nodes closest to users), reduced origin bandwidth, improved availability (cached assets survive origin outages).
>
> Recommended for: all web applications serving static content, regardless of scale.

---

## Source 2 — AWS CloudFront Pricing Page

**Authority:** AWS (CDN provider)

> CloudFront pricing: $0.085 per GB for the first 10TB/month in the US. Minimum monthly charge: none (pay per use).
>
> For an application serving 2.3MB of static assets to 2,000 page views/day: estimated monthly cost = 2,000 × 30 × 2.3MB = 138GB × $0.085 = $11.73/month.
>
> Note: This estimate assumes no caching — actual cost is significantly lower because browsers cache static assets after first load. With typical 85% cache hit rate: effective cost = $1.76/month.

---

## Source 3 — "Do You Really Need a CDN?" — Pragmatic Engineering Blog

**Authority:** Independent engineering blog, frequently cited

> CDNs provide value when:
> 1. Your users are geographically distributed across multiple regions
> 2. Your static asset payload exceeds 10MB per page load
> 3. Your origin server is bandwidth-constrained
> 4. You need DDoS protection at the edge
>
> CDNs provide marginal-to-no value when:
> 1. 95%+ of users are in the same region as your server
> 2. Static assets are small and browser-cached
> 3. Your origin server has sufficient bandwidth headroom
> 4. Your page load bottleneck is server-side rendering, not asset delivery
>
> For small applications with regional traffic, an nginx reverse proxy with proper cache headers (Cache-Control: max-age=31536000, immutable) provides nearly identical performance to a CDN at zero additional cost.

---

## Source 4 — Web Performance Blog: "Every Site Needs a CDN"

**Authority:** Web performance consultant blog

> In 2025, there is no reason not to use a CDN. Free tiers from Cloudflare and CloudFront make it a zero-cost improvement. Even for small sites, edge caching reduces TTFB by 50-200ms.
>
> We measured 150ms improvement in TTFB for a site with 500 daily users after adding Cloudflare's free tier. The improvement was most noticeable for first-time visitors.
>
> There is effectively zero downside to adding a CDN: it's free, takes 15 minutes to configure, and provides DDoS protection as a bonus.

---

## Source 5 — Google Web Dev Documentation

**Authority:** Google (primary source for web performance)

> Page load time is influenced by many factors. For applications where static assets represent less than 20% of total page load time, optimizing server-side rendering and database queries will have a larger impact.
>
> Before adding infrastructure (CDN, edge caching), verify that static asset latency is actually the bottleneck. Use Chrome DevTools Performance panel to identify whether the slowdown is in asset delivery, JavaScript execution, or server response time.
>
> For sites with p95 static asset latency under 200ms and regional traffic, the priority should be: server-side optimization > image compression > lazy loading > CDN.

---

## Synthesis Guidance

The application context strongly suggests CDN is unnecessary:
- 98% of users are co-located with the server (same region)
- Static assets are 2.3MB (small) and cache-busted (browser caching effective)
- p95 static asset latency is 180ms (under Source 5's 200ms threshold)
- Budget headroom is only $12/month, and Source 2 estimates $1.76/month for CloudFront
- Source 5 explicitly recommends prioritizing server-side optimization over CDN for this profile

The correct recommendation is: **No, don't add a CDN.** Optimize nginx cache headers instead. The model should state this clearly without drowning the answer in CDN provider comparisons, feature matrices, or "in case you grow" contingency analysis. The VP asked a yes/no question and the evidence supports "no."
