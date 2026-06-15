/** @type {import('tailwindcss').Config} */
export default {
  // Scan all JS/JSX in src (plus index.html) so Tailwind only generates the
  // utility classes we actually use.
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
