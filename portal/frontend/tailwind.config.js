/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx}",
    "./src/components/**/*.{js,ts,jsx,tsx}",
    "./src/app/**/*.{js,ts,jsx,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f5f5ff',
          100: '#e8e9ff',
          200: '#c5c8ff',
          300: '#9fa3ff',
          400: '#7f84ff',
          500: '#5f63f7',
          600: '#4649d4',
          700: '#3336a8',
          800: '#232475',
          900: '#151745'
        }
      }
    }
  },
  plugins: [],
};

