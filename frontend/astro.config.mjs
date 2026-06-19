// @ts-check
import { defineConfig, passthroughImageService } from 'astro/config'

import react from '@astrojs/react'
import icon from "astro-icon";

import tailwindcss from "@tailwindcss/vite"


import cloudflare from '@astrojs/cloudflare';


// https://astro.build/config
export default defineConfig({
  site: 'https://mkgg.dev',
  integrations: [react(), icon()],

  vite: {
      plugins: [tailwindcss()],
  },

  devToolbar: {
      enabled: false
  },

  image: {
      // Serve remote game icons directly (no /_image endpoint, no sharp).
      // The host (Cloudflare) can't run Astro's optimizer, and these
      // community-dragon icons are already small — so skip optimization.
      service: passthroughImageService(),
      domains: ['raw.communitydragon.org'],
  },

  adapter: cloudflare(),
});