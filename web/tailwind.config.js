/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // ONE type scale for the whole app, named for the job rather than the pixels — a
    // component asks for `text-label`, not `text-[11px]`. The old build drifted into
    // four different "small" sizes on a single screen because every file picked its own.
    fontSize: {
      micro: ["0.6875rem", { lineHeight: "1rem" }],      // 11px — captions, provenance
      label: ["0.75rem", { lineHeight: "1.125rem" }],    // 12px — form labels, table cells
      body: ["0.8125rem", { lineHeight: "1.375rem" }],   // 13px — prose, chat, buttons
      sub: ["0.9375rem", { lineHeight: "1.375rem" }],    // 15px — card titles
      title: ["1.125rem", { lineHeight: "1.625rem" }],   // 18px — view headings
      display: ["1.5rem", { lineHeight: "2rem" }],       // 24px — metric values
    },
    extend: {
      colors: {
        // Brand — Meridian Petroleum. Deep petrol blue, kept away from the semantic
        // hues below so chrome never reads as a verdict.
        brand: {
          50: "#eef4f8", 100: "#d3e3ed", 300: "#7ba7c4",
          500: "#2d6b91", 600: "#245a7b", 700: "#1b4664", 900: "#0f2a3d",
        },
        // Semantic — learned once, used everywhere: chart marks, badges, banners, lanes.
        // Each hue has a MARK shade (dots, rings, chart fills — the 3:1 graphics bar)
        // and, where the mark is too light for prose, a darker TEXT shade. Measured, not
        // eyeballed: the amber mark is 3.64:1 on white, which fails the 4.5:1 body-text
        // bar, so amber prose uses `text-suspect-text` at 5.27:1 instead.
        signal: { DEFAULT: "#2f855a", soft: "#e7f4ec" },    // on target / verified — 4.54:1
        suspect: { DEFAULT: "#b7791f", soft: "#fdf5e3",     // low-confidence inputs
                   text: "#946118" },                       // 5.27:1 on white, 4.86 on soft
        offtarget: { DEFAULT: "#c53030", soft: "#fdecec" }, // out of band / refused — 5.47:1
        // One hue per approval lane, in chain order, so a card's colour says where it is
        // before you read a word of it.
        stage: {
          draft: "#64748b", analyst: "#2d6b91", rm: "#5b53a6",
          site: "#b7791f", executed: "#2f855a", rejected: "#c53030",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"',
               "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)",
        panel: "0 12px 32px rgba(16,24,40,0.18)",
      },
    },
  },
  plugins: [],
};
