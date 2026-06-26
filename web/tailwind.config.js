/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // a calm slate/teal fleet-console palette (distinct from Mender's blue)
        ink: '#0f172a',
        panel: '#1e293b',
        edge: '#334155',
        accent: '#2dd4bf',
        muted: '#94a3b8',
      },
    },
  },
  plugins: [],
}
