/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic, not decorative: these are the verdict colours used across the
        // portfolio chart, the audit banners and the queue badges, so a reader learns
        // them once. green = trustworthy, amber = suspect inputs, red = off target.
        signal: "#2ca02c",
        suspect: "#ff7f0e",
        offtarget: "#d62728",
      },
      fontFamily: { mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"] },
    },
  },
  plugins: [],
};
