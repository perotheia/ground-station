/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Update-Factory dark-navy industrial palette (the modern dashboard look).
        ink: '#1A2634',       // primary background (deep navy/charcoal slate)
        panel: '#1F2D3D',     // panel/card surface (a hair lighter than ink)
        sidebar: '#141D29',   // sidebar + headers (darker navy tint)
        edge: '#2A3A4D',      // borders / dividers
        accent: '#1E88E5',    // vivid sky blue — active states, highlights
        ok: '#4CAF50',        // emerald — success, checkboxes
        danger: '#E57373',    // soft salmon — destructive (delete/trash)
        muted: '#90A4AE',     // cool grey — secondary text
      },
    },
  },
  plugins: [],
}
