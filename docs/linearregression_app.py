"""Kid-friendly Streamlit version of linearregressionex.py.

Same maths as the script (numpy.polyfit, degree 1) — but you can slide an age and
watch the straight line predict the height, then type the real height and see how
close the line was.

    streamlit run docs/linearregression_app.py
"""
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# ---- the same starting data as the script -----------------------------------
BASE_AGE = [2, 3, 4, 5, 6, 7, 8]
BASE_HEIGHT = [86, 95, 102, 109, 115, 122, 128]

MEASURED = "#2a78d6"        # categorical slot 1 — the points we measured
PREDICTED = "#eb6834"       # categorical slot 2 — a guess inside the ages we measured
BEYOND = "#4a3aa7"          # categorical slot 7 — a guess past the last dot
FAMILY = "#1baf7a"          # categorical slot 3 — the many-feature (family) model

# The made-up rule behind the practice cohort below. We invent it, generate kids
# from it, then let the maths rediscover it — so the learned numbers can be
# checked against the truth. Loosely calibrated on real 2–12yr growth.
TRUE_RULE = {"start": 73.0, "per_year": 6.5, "per_parent_cm": 0.45, "boy_bonus": 1.5}
MID_AVG = 168.0             # average mid-parental height the model centres on

st.set_page_config(page_title="Growing in a straight line", page_icon="📏",
                   layout="centered")
st.title("📏 Growing in a straight line")
st.caption("Every dot is a height we measured. The straight line is the best line "
           "that goes through all of them — that is what *linear* means. Move the "
           "slider to ask the line about any age.")

# ---- the kid's own measurements live here, added one at a time ---------------
if "extra" not in st.session_state:
    st.session_state.extra = []          # list of (age, height) the kid typed in

with st.sidebar:
    st.header("Add a real measurement")
    st.caption("Measured your height today? Add it and watch the line move.")
    new_age = st.number_input("Age (years)", 0.0, 20.0, 9.0, 0.5, key="na")
    new_height = st.number_input("Height (cm)", 40.0, 200.0, 134.0, 0.5, key="nh")
    if st.button("Add this dot", width="stretch"):
        st.session_state.extra.append((float(new_age), float(new_height)))
    if st.session_state.extra and st.button("Start over", width="stretch"):
        st.session_state.extra = []

    st.divider()
    st.header("Family features")
    st.caption("Used by the many-feature model further down the page.")
    dad = st.number_input("Father's height (cm)", 140.0, 215.0, 178.0, 0.5)
    mom = st.number_input("Mother's height (cm)", 130.0, 205.0, 165.0, 0.5)
    is_boy = st.radio("Child", ["Boy", "Girl"], horizontal=True) == "Boy"

ages = np.array(BASE_AGE + [a for a, _ in st.session_state.extra], dtype=float)
heights = np.array(BASE_HEIGHT + [h for _, h in st.session_state.extra], dtype=float)

# ---- the whole model: one line, y = m·x + c ---------------------------------
m, c = np.polyfit(ages, heights, 1)

st.subheader("Ask the line about an age")
a1, a2 = st.columns([2, 1])
slider_age = a1.slider("Slide to an age…", 1.0, 18.0, 15.0, 0.5)
typed = a2.text_input("…or type any age", value="", placeholder="e.g. 15",
                      help="Leave it blank to use the slider.")
ask_age, bad_input = slider_age, ""
if typed.strip():
    try:
        ask_age = float(typed.strip())
    except ValueError:
        bad_input = f"“{typed.strip()}” is not a number — using the slider instead."
if bad_input:
    st.info(bad_input)
if not 0 <= ask_age <= 30:                    # keep the chart readable
    st.info(f"Age {ask_age:g} is off the chart — showing the nearest age we can draw.")
    ask_age = min(max(ask_age, 0.0), 30.0)
guess = m * ask_age + c

# Inside the ages we actually measured the line is *interpolating* — safe-ish.
# Past the last dot it is *extrapolating* — same maths, much bolder claim, so it
# gets its own colour everywhere it appears.
oldest, youngest = float(ages.max()), float(ages.min())
beyond = ask_age > oldest or ask_age < youngest
who_guess = "Guess beyond our dots" if beyond else "Line's guess"

k1, k2, k3 = st.columns(3)
k1.metric(f"Line's guess at age {ask_age:g}", f"{guess:.1f} cm")
k2.metric("Grows per year (slope m)", f"{m:.2f} cm")
k3.metric("Height at age 0 (intercept c)", f"{c:.1f} cm")
st.caption(f"The line is **height = {m:.2f} × age + {c:.1f}** — "
           f"fitted on {len(ages)} dots with `numpy.polyfit(age, height, 1)`.")
if beyond:
    st.warning(f"🔮 Age {ask_age:g} is **past our last measured dot (age {oldest:g})**, "
               f"so {guess:.1f} cm is the purple *beyond our dots* guess — the line just "
               "keeps going straight. Real kids slow down and stop growing, so the "
               "further right you go, the more the line over-promises.")

# ---- chart: fitted line + measured dots + the one predicted dot --------------
x_lo, x_hi = float(min(youngest, ask_age)) - 0.5, float(max(oldest, ask_age)) + 0.5
solid_df = pd.DataFrame({"age": [youngest, oldest]})          # over the real dots
solid_df["height"] = m * solid_df["age"] + c
dashed_df = pd.DataFrame({"age": [x_lo, youngest, np.nan, oldest, x_hi]})  # past them
dashed_df["height"] = m * dashed_df["age"] + c
dots = pd.DataFrame({"age": ages, "height": heights, "who": "Measured"})
pred = pd.DataFrame({"age": [ask_age], "height": [guess], "who": who_guess})

guess_color = BEYOND if beyond else PREDICTED
scale = alt.Scale(domain=["Measured", "Line's guess", "Guess beyond our dots"],
                  range=[MEASURED, PREDICTED, BEYOND])
x = alt.X("age:Q", title="Age (years)",
          scale=alt.Scale(domain=[x_lo, x_hi], nice=False))
y = alt.Y("height:Q", title="Height (cm)", scale=alt.Scale(zero=False))

fit = alt.Chart(solid_df).mark_line(strokeWidth=2, color=PREDICTED,
                                    opacity=0.7).encode(x=x, y=y)
fit_beyond = alt.Chart(dashed_df).mark_line(strokeWidth=2, color=BEYOND,
                                            strokeDash=[6, 4], opacity=0.7).encode(x=x, y=y)
drop = alt.Chart(pred).mark_rule(strokeDash=[3, 3], color=guess_color,
                                 opacity=0.6).encode(x=x, y=y)
measured = alt.Chart(dots).mark_point(size=110, filled=True, stroke="white",
                                      strokeWidth=2).encode(
    x=x, y=y, color=alt.Color("who:N", scale=scale, title=None),
    tooltip=[alt.Tooltip("age:Q", title="Age"),
             alt.Tooltip("height:Q", title="Height (cm)", format=".1f")])
guess_dot = alt.Chart(pred).mark_point(size=220, shape="diamond", filled=True,
                                       stroke="white", strokeWidth=2).encode(
    x=x, y=y, color=alt.Color("who:N", scale=scale, title=None),
    tooltip=[alt.Tooltip("age:Q", title="Age"),
             alt.Tooltip("height:Q", title="Guess (cm)", format=".1f")])
label = alt.Chart(pred).mark_text(dy=-18, fontSize=13, fontWeight="bold").encode(
    x=x, y=y, text=alt.Text("height:Q", format=".1f"))

st.altair_chart(
    (fit + fit_beyond + drop + measured + guess_dot + label).properties(height=420),
    width="stretch")
st.caption("Blue dots = heights we really measured. The **orange** line runs through "
           "them; where it carries on **dashed purple**, the line is guessing past "
           "everything we know.")

# ---- show the working: every column of height = m × age + c ------------------
st.subheader("🧮 How the formula gets that number")
st.markdown(
    f"### {guess:.1f} cm  =  {m:.2f} × {ask_age:g}  +  {c:.1f}\n"
    f"**height = m × age + c** — take the age, multiply by **m = {m:.2f} cm** (how "
    f"much you grow in one year), then add **c = {c:.1f} cm** (where the line starts). "
    "Nothing else. That one multiply-and-add is the whole idea of *linear*.")

real = dict(zip(ages.tolist(), heights.tolist()))       # age → measured height
rows_age = sorted(set(ages.tolist()) | {ask_age})
work = pd.DataFrame({"Age (years)": rows_age})
work["× slope m"] = round(float(m), 2)
work["= m × Age"] = (m * work["Age (years)"]).round(1)
work["+ intercept c"] = round(float(c), 1)
work["= Predicted height (cm)"] = (m * work["Age (years)"] + c).round(1)
work["Real measured height (cm)"] = [real.get(a, np.nan) for a in rows_age]
work["Line off by (cm)"] = (work["Real measured height (cm)"]
                            - work["= Predicted height (cm)"]).round(1)
work["Row"] = ["👈 your age" if a == ask_age else "measured dot" for a in rows_age]
st.dataframe(work, width="stretch", hide_index=True, column_config={
    "Age (years)": st.column_config.NumberColumn(
        help="The input — the only thing we feed the formula. Called x."),
    "× slope m": st.column_config.NumberColumn(
        help=f"m = {m:.2f}. The same number on every row: cm gained per year of age. "
             "Worked out below from all the dots at once."),
    "= m × Age": st.column_config.NumberColumn(
        help="Age multiplied by the slope — how much of the height comes from "
             "having lived that many years."),
    "+ intercept c": st.column_config.NumberColumn(
        help=f"c = {c:.1f}. Also the same on every row: where the line crosses age 0. "
             "It shifts the whole line up or down."),
    "= Predicted height (cm)": st.column_config.NumberColumn(
        help="m × Age + c. The line's answer — a point exactly ON the line."),
    "Real measured height (cm)": st.column_config.NumberColumn(
        help="What we actually measured. Blank for an age nobody has measured."),
    "Line off by (cm)": st.column_config.NumberColumn(
        help="Real − Predicted. The 'residual'. Positive = taller than the line "
             "said. These are the misses that m and c were chosen to make small."),
    "Row": st.column_config.TextColumn(help="Where this row came from.")})
st.caption("Read a row left to right and you have done the maths yourself. "
           "Hover the **?** on any column header for what it is. The last column is "
           "how far the line missed a dot — small numbers everywhere mean the growth "
           "really is close to a straight line.")

with st.expander("📖 What every column means (in words)"):
    st.dataframe(pd.DataFrame([
        ("Age (years)", "x — the input", "You choose it, or it's a day we measured."),
        ("× slope m", "the tilt of the line", f"One number for the whole line: {m:.2f} "
         "cm per year. Calculated below from every dot at once."),
        ("= m × Age", "the age part of the answer", f"{m:.2f} × the age in that row."),
        ("+ intercept c", "the starting height", f"{c:.1f} cm — where the line would "
         "sit at age 0. It slides the line up/down without tilting it."),
        ("= Predicted height (cm)", "y — the line's answer", "m × Age + c. Add the two "
         "pieces to the left."),
        ("Real measured height (cm)", "the truth", "The tape-measure number. Empty "
         "when nobody measured that age."),
        ("Line off by (cm)", "the residual", "Real − Predicted. How wrong the line "
         "was on that dot. Zero = the dot sits exactly on the line."),
        ("Row", "where it came from", "A measured dot, or the age you asked about."),
    ], columns=["Column", "Its proper name", "What it is and where it comes from"]),
        width="stretch", hide_index=True)

step = pd.DataFrame({"Age (years)": [ask_age - 1, ask_age, ask_age + 1]})
step["Predicted height (cm)"] = (m * step["Age (years)"] + c).round(1)
step["One more year adds (cm)"] = step["Predicted height (cm)"].diff().round(2)
st.markdown("**Why it is a straight line:** every extra year adds the *same* "
            f"**{m:.2f} cm** — never more, never less.")
st.dataframe(step, width="stretch", hide_index=True)

# ---- where m and c actually come from (least squares, by hand) ---------------
st.subheader("🔍 Where do m and c come from?")
xbar, ybar = float(ages.mean()), float(heights.mean())
dx, dy = ages - xbar, heights - ybar
sum_prod, sum_sq = float((dx * dy).sum()), float((dx * dx).sum())
m_hand = sum_prod / sum_sq
c_hand = ybar - m_hand * xbar

st.markdown(
    f"`np.polyfit` isn't magic — it picks the **one** line that misses the dots by "
    f"the least, and there is a recipe for it. First the two averages: mean age "
    f"**x̄ = {xbar:.2f}**, mean height **ȳ = {ybar:.2f} cm**. Then measure every dot "
    "*from those averages* and fill in two columns:")

# Everything is text so the bold TOTAL row can sit under the numbers.
ls = pd.DataFrame({
    "Age  x": [f"{a:g}" for a in ages],
    "Height  y": [f"{h:g}" for h in heights],
    "x − x̄": [f"{v:+.2f}" for v in dx],
    "y − ȳ": [f"{v:+.2f}" for v in dy],
    "(x − x̄) × (y − ȳ)": [f"{v:+.2f}" for v in dx * dy],
    "(x − x̄)²": [f"{v:.2f}" for v in dx * dx]})
ls.loc[len(ls)] = ["TOTAL (Σ)", "", "", "", f"{sum_prod:+.2f}", f"{sum_sq:.2f}"]
st.dataframe(ls, width="stretch", hide_index=True, column_config={
    "Age  x": st.column_config.Column(help="The dot's age."),
    "Height  y": st.column_config.Column(help="The dot's measured height."),
    "x − x̄": st.column_config.Column(
        help="How far this dot sits left/right of the average age. Negative = younger "
             "than average."),
    "y − ȳ": st.column_config.Column(
        help="How far this dot sits below/above the average height."),
    "(x − x̄) × (y − ȳ)": st.column_config.Column(
        help="The two distances multiplied. Big and positive when older ALSO means "
             "taller — this column is what detects that age and height move together."),
    "(x − x̄)²": st.column_config.Column(
        help="How spread out the ages are, on their own. It's the yardstick we divide "
             "by, so the slope comes out in cm per YEAR.")})

st.markdown(
    f"**Slope:**  m = Σ(x − x̄)(y − ȳ) ÷ Σ(x − x̄)²  =  **{sum_prod:.2f} ÷ "
    f"{sum_sq:.2f} = {m_hand:.4f} cm per year**\n\n"
    f"> *In words:* how much age and height rise together, divided by how spread out "
    f"the ages are on their own. Divide by that spread and the answer lands in "
    f"cm **per year** instead of just 'a big number'.\n\n"
    f"**Intercept:**  c = ȳ − m × x̄  =  {ybar:.2f} − {m_hand:.4f} × {xbar:.2f} = "
    f"**{c_hand:.2f} cm**\n\n"
    f"> *In words:* the line is forced to pass through the average dot "
    f"(x̄ = {xbar:.2f}, ȳ = {ybar:.2f}) — every least-squares line does. Once you know "
    f"the tilt, slide the line down from that average point back to age 0 and c is "
    f"where you land. That's why c ({c_hand:.1f} cm) is a real newborn-ish height "
    "here, but it would be nonsense if we'd only measured teenagers.")
st.success(f"Hand-calculated m = {m_hand:.4f}, c = {c_hand:.4f} · "
           f"`np.polyfit` gave m = {m:.4f}, c = {c:.4f} — the same line. "
           "The library just does these two sums for you.")

# --- why "least squares"? try other slopes and watch the misses grow ----------
st.markdown("**Why this line and not another one?** Because it makes the *total "
            "squared miss* as small as possible. Try tilting it:")
def squared_miss(slope):
    """Total (real − predicted)² if the line had this slope (best intercept for it)."""
    return float((((slope * ages + (ybar - slope * xbar)) - heights) ** 2).sum())


trials = [m_hand - 1, m_hand - 0.5, m_hand, m_hand + 0.5, m_hand + 1]
st.dataframe(pd.DataFrame({
    "Slope tried (cm/year)": [round(t, 2) for t in trials],
    "Best matching intercept (cm)": [round(ybar - t * xbar, 1) for t in trials],
    "Total squared miss": [round(squared_miss(t), 1) for t in trials],
    "": ["", "", "👈 the winner — np.polyfit's answer", "", ""]}),
    width="stretch", hide_index=True, column_config={
    "Total squared miss": st.column_config.NumberColumn(
        help="Add up (real − predicted)² for all the dots. Squaring makes every miss "
             "count as positive, and punishes one big miss more than several small "
             "ones. The winning line is the one with the smallest total — that is "
             "literally what 'least squares' means.")})

# ---- more than one feature: age + parents + sex ------------------------------
st.divider()
st.subheader("👨‍👩‍👧 Adding more features")
st.markdown(
    "Age alone is one feature. Real growth also runs in the family — so let's give "
    "the formula **three** features and watch it grow extra columns:\n\n"
    "`height = start + a×Age + b×(Parents' height) + c×(Boy?)`\n\n"
    "Same idea as before — multiply each feature by its own number and add them "
    "all up. That is *multiple* linear regression.")


@st.cache_data
def practice_cohort(n=400, seed=7):
    """400 pretend children built from TRUE_RULE, so we know the right answer."""
    rng = np.random.default_rng(seed)
    age = rng.uniform(2, 12, n)
    mid = rng.normal(MID_AVG, 6, n)                 # their mid-parental height
    boy = rng.integers(0, 2, n).astype(float)
    height = (TRUE_RULE["start"] + TRUE_RULE["per_year"] * age
              + TRUE_RULE["per_parent_cm"] * (mid - MID_AVG)
              + TRUE_RULE["boy_bonus"] * boy
              + rng.normal(0, 2.5, n))              # everything we can't explain
    return pd.DataFrame({"age": age, "mid": mid, "boy": boy, "height": height})


kids = practice_cohort()
X = np.column_stack([np.ones(len(kids)), kids["age"], kids["mid"] - MID_AVG, kids["boy"]])
coef, *_ = np.linalg.lstsq(X, kids["height"].to_numpy(), rcond=None)
b_start, b_age, b_parent, b_boy = coef

st.markdown("**Step 1 — the maths learns each feature's number** from 400 practice "
            "children (we invented the rule, so you can mark its homework):")
st.dataframe(pd.DataFrame({
    "Feature": ["Start (before any feature)", "Each year of age",
                "Each cm the parents are above average", "Being a boy"],
    "Real rule we used (cm)": [TRUE_RULE["start"], TRUE_RULE["per_year"],
                               TRUE_RULE["per_parent_cm"], TRUE_RULE["boy_bonus"]],
    "What the maths worked out (cm)": np.round(coef, 2)}),
    width="stretch", hide_index=True, column_config={
    "Feature": st.column_config.TextColumn(
        help="One thing we know about a child. Each gets its own number to be "
             "multiplied by — its 'coefficient'. Age's coefficient is the slope m "
             "from the top of the page; there is now one of those per feature."),
    "Real rule we used (cm)": st.column_config.NumberColumn(
        help="The number I secretly used to invent the 400 children. Normally you "
             "never get to see this — that's the whole point of the exercise."),
    "What the maths worked out (cm)": st.column_config.NumberColumn(
        help="What np.linalg.lstsq recovered knowing ONLY the children's ages, "
             "parents and heights. Same least-squares idea as m and c above — pick "
             "the numbers that make the total squared miss smallest — just solved "
             "for four numbers at once instead of two.")})
st.caption("Close, but never exact — every child also has a bit of pure randomness "
           "the formula can't see. More children ⇒ closer numbers.")

# --- step 2: this child's own row, feature by feature -------------------------
mid_parent = (dad + mom) / 2
parts = [("Start (before any feature)", "—", b_start, b_start),
         ("Age", f"{ask_age:g} years", b_age, b_age * ask_age),
         (f"Parents (mid-parental {mid_parent:.1f} cm − average {MID_AVG:g})",
          f"{mid_parent - MID_AVG:+.1f} cm", b_parent, b_parent * (mid_parent - MID_AVG)),
         ("Being a boy", "yes" if is_boy else "no", b_boy, b_boy * float(is_boy))]
family_guess = sum(p[3] for p in parts)

st.markdown("**Step 2 — plug in *your* child** and add the columns up:")
st.dataframe(pd.DataFrame({
    "Feature": [p[0] for p in parts],
    "This child's value": [p[1] for p in parts],
    "× its number (cm)": [round(p[2], 2) for p in parts],
    "= adds to the height (cm)": [round(p[3], 1) for p in parts]}),
    width="stretch", hide_index=True, column_config={
    "Feature": st.column_config.TextColumn(
        help="The four ingredients. 'Start' is the intercept c — the same idea as "
             "before, the height left over when every other feature is zero."),
    "This child's value": st.column_config.TextColumn(
        help="Your child's actual number for that feature. Parents' height is "
             f"measured as a distance from the {MID_AVG:g} cm average, so a taller-"
             "than-average family gives a plus and a shorter one a minus."),
    "× its number (cm)": st.column_config.NumberColumn(
        help="The coefficient learned in step 1 — how many cm this feature is worth "
             "per unit. Fixed for every child."),
    "= adds to the height (cm)": st.column_config.NumberColumn(
        help="Value × its number. Add this whole column top to bottom and you have "
             "the prediction — exactly like m × Age + c, just with more pieces.")})
st.caption(f"Those four rows add up to **{family_guess:.1f} cm**. Notice the shape is "
           "identical to the one-feature formula — multiply each feature by its own "
           "number, then add. Every extra feature is one more row, never a new idea.")

f1, f2, f3 = st.columns(3)
f1.metric(f"Age only, at {ask_age:g}", f"{guess:.1f} cm")
f2.metric(f"With family features", f"{family_guess:.1f} cm",
          f"{family_guess - guess:+.1f} cm vs age only")
adult = (dad + mom + (13 if is_boy else -13)) / 2
f3.metric("Grown-up height (Tanner)", f"{adult:.0f} cm", "± 8.5 cm", delta_color="off")
if ask_age > 12:
    st.warning(f"Same trap as before: the 400 practice children are all aged 2–12, so "
               f"at age {ask_age:g} *both* lines are guessing past their data. Notice "
               "they still climb forever — which is why the grown-up estimate on the "
               "right comes from a different formula, not from the line.")
st.caption("The last one is the real clinical **mid-parental target height**: "
           f"(father + mother {'+' if is_boy else '−'} 13) ÷ 2 — the one family "
           "formula paediatricians actually use.")

# --- step 3: both lines on one chart ------------------------------------------
grid = pd.DataFrame({"age": np.linspace(max(1.0, x_lo), x_hi, 60)})
fam_line = grid.assign(
    height=b_start + b_age * grid["age"] + b_parent * (mid_parent - MID_AVG)
    + b_boy * float(is_boy), who="With family features")
age_line = grid.assign(height=m * grid["age"] + c, who="Age only")
two = pd.concat([age_line, fam_line])
scale2 = alt.Scale(domain=["Measured", "Age only", "With family features"],
                   range=[MEASURED, PREDICTED, FAMILY])
st.altair_chart(
    (alt.Chart(two).mark_line(strokeWidth=2).encode(
        x=alt.X("age:Q", title="Age (years)", scale=alt.Scale(domain=[x_lo, x_hi], nice=False)),
        y=alt.Y("height:Q", title="Height (cm)", scale=alt.Scale(zero=False)),
        color=alt.Color("who:N", scale=scale2, title=None),
        tooltip=["who:N", alt.Tooltip("age:Q", title="Age", format=".1f"),
                 alt.Tooltip("height:Q", title="Height (cm)", format=".1f")])
     + alt.Chart(dots).mark_point(size=110, filled=True, stroke="white",
                                  strokeWidth=2).encode(
        x="age:Q", y="height:Q", color=alt.Color("who:N", scale=scale2, title=None),
        tooltip=[alt.Tooltip("age:Q", title="Age"),
                 alt.Tooltip("height:Q", title="Height (cm)", format=".1f")])
     ).properties(height=380), width="stretch")
st.caption("Change the parents' heights in the sidebar and the **green** line lifts "
           "or drops — that is the parent feature doing its job. The orange line "
           "can't move, because it has never heard of parents.")

with st.expander("What about race, ethnicity or background?"):
    st.markdown(
        "Deliberately left out, and that is worth explaining to a curious kid.\n\n"
        "- A feature only belongs in the formula if it **causes** the thing you are "
        "predicting. Parents' heights do — genes are handed down. Race is a social "
        "label, not a growth mechanism, so a coefficient on it would really be "
        "measuring nutrition, income, health care and history, all wearing a "
        "disguise.\n"
        "- Medicine has been actively **removing** these adjustments for exactly that "
        "reason (kidney eGFR and lung-function equations both dropped theirs). The "
        "WHO growth standards are one single chart for every child on earth.\n"
        "- And practically: we have no honest data to fit such a number on, so any "
        "value here would be invented. A made-up coefficient is worse than no "
        "coefficient.\n\n"
        "If you do have real data with a background column, the same "
        "`np.linalg.lstsq` call above takes it as one more column — the maths is "
        "indifferent. Deciding which features *deserve* to be in the formula is the "
        "human's job, and it is the more important half of the work.")

# ---- a quick peek at the ages we have never measured -------------------------
future = [a for a in range(int(oldest) + 1, 19)]
if future:
    st.subheader("🔮 What the line says about the years ahead")
    st.caption(f"Straight-line guesses — every one of these is past age {oldest:g}, "
               "so they are the purple kind.")
    st.dataframe(pd.DataFrame({"Age (years)": future,
                               "Line's guess (cm)": [round(m * a + c, 1) for a in future],
                               "Kind": "beyond our dots"}),
                 width="stretch", hide_index=True)

# ---- how close was the line? -------------------------------------------------
if st.session_state.extra:
    st.subheader("How close was the line?")
    rows = [{"Age": a, "Real height (cm)": h,
             "Line said (cm)": round(m * a + c, 1),
             "Off by (cm)": round(h - (m * a + c), 1)}
            for a, h in st.session_state.extra]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    worst = max(abs(r["Off by (cm)"]) for r in rows)
    st.caption(f"Biggest miss so far: **{worst:.1f} cm**. The smaller these are, the "
               "better a straight line describes how you grow.")

with st.expander("See all the numbers"):
    st.dataframe(pd.DataFrame({"Age (years)": ages, "Height (cm)": heights,
                               "On the line (cm)": (m * ages + c).round(1)}),
                 width="stretch", hide_index=True)
