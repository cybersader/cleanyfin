// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightImageZoom from 'starlight-image-zoom';
import remarkObsidianCallout from 'remark-obsidian-callout';
import remarkWikiLink from 'remark-wiki-link';
import rehypeExternalLinks from 'rehype-external-links';

// https://astro.build/config
export default defineConfig({
  site: 'https://cybersader.github.io',
  base: '/cleanyfin',
  vite: {
    server: {
      // Allow Docker / Tailscale / LAN / cross-machine previews.
      // Vite 6+ blocks non-localhost Host headers by default.
      allowedHosts: true,
    },
  },
  markdown: {
    remarkPlugins: [remarkObsidianCallout, [remarkWikiLink, { aliasDivider: '|' }]],
    rehypePlugins: [
      [rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }],
    ],
  },
  integrations: [
    starlight({
      title: 'cleanyfin',
      description:
        'An open-source, self-hosted content-filtering layer for Jellyfin, backed by a federated, crowdsourced database of tagged content segments.',
      lastUpdated: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/cybersader/cleanyfin' },
      ],
      editLink: {
        baseUrl: 'https://github.com/cybersader/cleanyfin/edit/main/docs/',
      },
      plugins: [starlightImageZoom()],
      sidebar: [
        { label: 'Start here', link: '/start-here/' },
        { label: 'Vision', autogenerate: { directory: 'vision' } },
        { label: 'Design', autogenerate: { directory: 'design' } },
        { label: 'Project', autogenerate: { directory: 'project' } },
        { label: 'Research (deep dives)', autogenerate: { directory: 'research' }, collapsed: true },
      ],
    }),
  ],
});
