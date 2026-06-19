import type { IconMap, SocialLink, Site } from '@/types'

export const SITE: Site = {
  title: 'HyperTFT',
  description: 'Find meta TFT comps',
  href: 'https://mkgg.dev',
  author: 'mk-gg',
  locale: 'en-US',
}

export const NAV_LINKS: SocialLink[] = [
  {
    href: '/tierlist',
    label: 'tierlist',
  },
  {
    href: '/builder',
    label: 'builder',
  },
]

export const SOCIAL_LINKS: SocialLink[] = [
  {
    href: 'https://github.com/mk-gg',
    label: 'GitHub',
  },
]

export const ICON_MAP: IconMap = {
  Website: 'lucide:globe',
  GitHub: 'simple-icons:github',
  LinkedIn: 'simple-icons:linkedin',
  Instagram: 'simple-icons:instagram',
  Twitter: 'simple-icons:twitter',
  Email: 'lucide:mail',
  RSS: 'lucide:rss',
}
