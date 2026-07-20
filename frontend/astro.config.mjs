// @ts-check
import { defineConfig, passthroughImageService } from 'astro/config'

import react from '@astrojs/react'
import icon from "astro-icon";

import tailwindcss from "@tailwindcss/vite"


// https://astro.build/config
export default defineConfig({
    site: 'https://hypertft.mkgg.dev',
    integrations: [react(), icon()],
    vite: {
        plugins: [tailwindcss()],
    },
    devToolbar: {
        enabled: false
    },
    image: {
        domains: ['raw.communitydragon.org'],
    },
});
