/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        aether: {
          bg: '#070B12',
          bgSecondary: '#0B111A',
          card: 'rgba(20, 27, 38, 0.75)',
          cardSolid: '#141B26',
          cardHover: 'rgba(28, 38, 54, 0.85)',
          cyan: '#16E6F5',
          cyanSoft: '#50F5FF',
          blue: '#6C7CFF',
          textPrimary: '#F3F7FA',
          textSecondary: '#91A1B3',
          border: 'rgba(255, 255, 255, 0.08)',
          borderHover: 'rgba(22, 230, 245, 0.35)',
          glowCyan: 'rgba(22, 230, 245, 0.15)',
        },
        ink: {
          900: '#070B12',
          800: '#0B111A',
          700: '#141B26',
        },
        paper: {
          50: '#F3F7FA',
        },
        horizon: {
          300: '#91A1B3',
          600: '#6C7CFF',
        },
        stamp: {
          500: '#16E6F5',
        },
        brass: {
          400: '#50F5FF',
        },
        signal: {
          red: '#FF4D6D',
          green: '#00F5D4',
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        display: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      boxShadow: {
        'cyan-glow': '0 0 25px -5px rgba(22, 230, 245, 0.25)',
        'cyan-glow-sm': '0 0 15px -3px rgba(22, 230, 245, 0.2)',
        'blue-glow': '0 0 25px -5px rgba(108, 124, 255, 0.25)',
        'glass-card': '0 8px 32px 0 rgba(0, 0, 0, 0.37)',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
}
