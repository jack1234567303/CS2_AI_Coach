/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0f1419',
        panel: '#1a2230',
        accent: '#4ade80',
        warn: '#fbbf24',
        danger: '#f87171',
      },
    },
  },
  plugins: [],
}
