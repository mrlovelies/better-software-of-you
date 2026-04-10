/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        harvest: {
          50: '#f0f7e8',
          100: '#dcecc8',
          200: '#b8d990',
          300: '#8fc05a',
          400: '#6ba632',
          500: '#2d5016',
          600: '#264412',
          700: '#1e360e',
          800: '#17280a',
          900: '#0f1a07',
        },
      },
    },
  },
  plugins: [],
}
