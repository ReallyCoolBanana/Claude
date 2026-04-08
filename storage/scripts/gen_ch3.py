#!/usr/bin/env python3
"""Generate Chapter 3: Control Flow Super PDF."""
import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
E = []

E.append(Paragraph("Chapter 3: Control Flow", styles['ChapterTitle']))
E.append(Paragraph("Relational graph edition. Pull-shaped. Top-down.", styles['Subtitle']))

E.append(Paragraph("How to use this document", styles['SectionHead']))
E.append(Paragraph("Red = checkpoint (answer in chat). Green = practice. Blue = target. Walk edges in order.", styles['Body']))

E.append(Paragraph("Big picture", styles['SectionHead']))
E.append(Paragraph(
    "Control flow is the routing table of your program. Every program is a sequence of decisions "
    "(if/switch) and repetitions (while/for/do). This chapter gives you exactly 6 constructs. "
    "Picking the right one for each situation is the skill.", styles['Body']))

E.append(Paragraph("Target problem", styles['SectionHead']))
E.append(target_box([
    Paragraph("<b>Write a simple integer calculator that reads lines of the form "
              "<font name='Courier'>operand operator operand</font> (e.g., 42 + 17), "
              "dispatches on the operator using switch, loops until EOF, "
              "and handles bad input with else-if chains.</b>", styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.15*inch))

E.append(Paragraph("The Graph", styles['SectionHead']))
E.append(Paragraph("Node inventory", styles['SubHead']))
E.append(make_table(
    ["Node", "Syntax", "When to use"],
    [
        ["if-else", "if (expr) stmt else stmt", "Two-way decision"],
        ["else-if", "if ... else if ... else", "Multi-way decision with complex conditions"],
        ["switch", "switch (expr) { case: ... }", "Multi-way on a single integer/char value"],
        ["while", "while (expr) stmt", "Loop with test at top, may execute 0 times"],
        ["for", "for (init; test; incr) stmt", "Counted/indexed loops (most common loop)"],
        ["do-while", "do stmt while (expr);", "Loop with test at bottom, always executes once"],
        ["break", "break;", "Exit innermost loop or switch immediately"],
        ["continue", "continue;", "Skip to next iteration of innermost loop"],
        ["goto", "goto label;", "Jump anywhere in function (almost never needed)"],
    ],
    col_widths=[1.0*inch, 2.5*inch, 3.0*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Decision graph -- which construct to use", styles['SubHead']))
E.append(concept_box([
    Paragraph("<b>Q: How many branches?</b>", styles['CalloutText']),
    Paragraph("2 branches &rarr; if-else", styles['CalloutText']),
    Paragraph("3+ branches on same int/char value &rarr; switch", styles['CalloutText']),
    Paragraph("3+ branches on different conditions &rarr; else-if chain", styles['CalloutText']),
    Paragraph("<b>Q: How many iterations?</b>", styles['CalloutText']),
    Paragraph("Known count / index variable &rarr; for", styles['CalloutText']),
    Paragraph("Unknown count, may be zero &rarr; while", styles['CalloutText']),
    Paragraph("Must execute at least once &rarr; do-while", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── ZOOM IN ──
E.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

E.append(Paragraph("Edge 1: if-else", styles['SubHead']))
E.append(Paragraph(
    "The expression is evaluated. If nonzero (true), the first statement executes. "
    "If zero (false), the else branch executes (if present). "
    "<b>Dangling else trap:</b> an else binds to the nearest preceding if without an else.",
    styles['Body']))
E.append(Paragraph('if (n &gt; 0)', styles['Code']))
E.append(Paragraph('    result = positive;', styles['Code']))
E.append(Paragraph('else', styles['Code']))
E.append(Paragraph('    result = non_positive;', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 3.1:</b> Write an if-else that sets <font name='Courier'>sign</font> to "
              "-1, 0, or 1 depending on whether x is negative, zero, or positive.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>if (x &lt; 0) sign = -1; else if (x == 0) sign = 0; else sign = 1;</font>",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 2: switch", styles['SubHead']))
E.append(Paragraph(
    "Tests a single integer/char expression against constant cases. Execution falls through from one case "
    "to the next unless you use <font name='Courier'>break</font>. "
    "<font name='Courier'>default</font> catches everything not matched.",
    styles['Body']))
E.append(Paragraph('switch (operator) {', styles['Code']))
E.append(Paragraph("case '+': result = a + b; break;", styles['Code']))
E.append(Paragraph("case '-': result = a - b; break;", styles['Code']))
E.append(Paragraph("case '*': result = a * b; break;", styles['Code']))
E.append(Paragraph("case '/': result = (b != 0) ? a/b : 0; break;", styles['Code']))
E.append(Paragraph('default: printf("bad op\\n"); break;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph(
    "<b>Fall-through is intentional in C.</b> Forgetting break is a common bug. "
    "Intentional fall-through should have a comment.", styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 3.2:</b> Write a switch that counts vowels and consonants in input. "
              "Use fall-through to group vowel cases.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>case 'a': case 'e': case 'i': case 'o': case 'u': vowels++; break;</font> "
              "default handles consonants.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 3: while loop", styles['SubHead']))
E.append(Paragraph(
    "Test at the top. If the condition is false initially, the body never executes. "
    "Use when you do not know how many iterations in advance.",
    styles['Body']))
E.append(Paragraph('int c;', styles['Code']))
E.append(Paragraph('while ((c = getchar()) != EOF) {', styles['Code']))
E.append(Paragraph('    /* process c */', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 3.3:</b> Use a while loop to reverse the digits of an integer n. "
              "(Hint: extract last digit with n%10, remove it with n/10.)", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>int rev=0; while(n&gt;0){rev=rev*10+n%10; n/=10;}</font>",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 4: for loop", styles['SubHead']))
E.append(Paragraph(
    "The most common loop. All loop machinery (init, test, increment) on one line. "
    "Preferred for counted loops and array traversals. "
    "Any of the three parts can be omitted (infinite loop: <font name='Courier'>for(;;)</font>).",
    styles['Body']))
E.append(Paragraph('for (int i = 0; i &lt; n; i++)', styles['Code']))
E.append(Paragraph('    printf("%d\\n", a[i]);', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 3.4:</b> Write a for loop that computes n factorial.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>long fact=1; for(int i=2; i&lt;=n; i++) fact*=i;</font>",
              styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Without looking up: when do you use switch vs else-if? When for vs while? "
              "What happens if you forget break in a switch case?", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Edge 5: do-while", styles['SubHead']))
E.append(Paragraph(
    "Body executes at least once, then tests. Rare in practice -- most common use is "
    "digit-by-digit number processing (you always have at least one digit, even for 0).",
    styles['Body']))
E.append(Paragraph('/* Convert int n to string s */', styles['Code']))
E.append(Paragraph('int i = 0;', styles['Code']))
E.append(Paragraph('do {', styles['Code']))
E.append(Paragraph("    s[i++] = n % 10 + '0';", styles['Code']))
E.append(Paragraph('} while ((n /= 10) > 0);', styles['Code']))
E.append(Paragraph("s[i] = '\\0';", styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 3.5:</b> Why would a while loop fail for the itoa conversion above when n is 0?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>while(n&gt;0)</font> would never enter the body for n=0, "
              "producing an empty string. do-while always produces at least '0'.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 6: break and continue", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>break</font> exits the innermost enclosing loop or switch. "
    "<font name='Courier'>continue</font> skips the rest of the current iteration and jumps to "
    "the next test (while/do) or increment (for). Neither affects outer loops.",
    styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 3.6:</b> Write a loop that reads ints from input and sums only the positive ones. "
              "Use continue to skip negatives. Stop on 0.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>while(scanf(\"%d\",&amp;n)==1){if(n==0)break; if(n&lt;0)continue; sum+=n;}</font>",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 7: goto (rarely needed)", styles['SubHead']))
E.append(Paragraph(
    "Only legitimate use: breaking out of deeply nested loops. In all other cases, "
    "break/continue/return/restructuring is clearer. If you use goto, something is probably wrong with your design.",
    styles['Body']))
E.append(PageBreak())

# ── SOLVE TARGET ──
E.append(Paragraph("Solving the target problem", styles['SectionHead']))
E.append(Paragraph("Walk: E3 (while for input loop) + E2 (switch for dispatch) + E1 (else-if for error handling)", styles['Body']))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int main(void) {', styles['Code']))
E.append(Paragraph('    int a, b;', styles['Code']))
E.append(Paragraph('    char op;', styles['Code']))
E.append(Paragraph('    while (scanf("%d %c %d", &amp;a, &amp;op, &amp;b) == 3) {', styles['Code']))
E.append(Paragraph('        switch (op) {', styles['Code']))
E.append(Paragraph("        case '+': printf(\"%d\\n\", a + b); break;", styles['Code']))
E.append(Paragraph("        case '-': printf(\"%d\\n\", a - b); break;", styles['Code']))
E.append(Paragraph("        case '*': printf(\"%d\\n\", a * b); break;", styles['Code']))
E.append(Paragraph("        case '/':", styles['Code']))
E.append(Paragraph("            if (b == 0)", styles['Code']))
E.append(Paragraph('                printf("error: divide by zero\\n");', styles['Code']))
E.append(Paragraph("            else", styles['Code']))
E.append(Paragraph('                printf("%d\\n", a / b);', styles['Code']))
E.append(Paragraph("            break;", styles['Code']))
E.append(Paragraph('        default:', styles['Code']))
E.append(Paragraph("            printf(\"unknown operator: %c\\n\", op);", styles['Code']))
E.append(Paragraph("            break;", styles['Code']))
E.append(Paragraph('        }', styles['Code']))
E.append(Paragraph('    }', styles['Code']))
E.append(Paragraph('    return 0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("Add support for the % (modulus) operator to the calculator. "
              "Handle the divide-by-zero case the same way. Report your modified switch.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── FINAL EXAM ──
E.append(Paragraph("Final exam", styles['SectionHead']))
for i, q in enumerate([
    "Write a function <font name='Courier'>void itoa(int n, char s[])</font> that converts integer n to string. Handle negative numbers.",
    "Write a function <font name='Courier'>void reverse(char s[])</font> that reverses string s in place.",
    "Write a binary search function using a while loop (not for).",
    "Rewrite the calculator to also handle parenthesized single operations like (3 + 4).",
    "Write a function <font name='Courier'>void shellsort(int v[], int n)</font> using nested for loops with a gap sequence.",
], start=1):
    E.append(KeepTogether([problem_box([
        Paragraph(f"<b>Problem {i}:</b> {q}", styles['CalloutText']),
    ], styles)]))
    E.append(Spacer(1, 0.05*inch))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("Solve at least 3 of the 5 problems. Report in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Answer key", styles['SectionHead']))
for i, a in enumerate([
    "itoa: Walk E5 (do-while for digit extraction) + E1 (if for negative). Handle sign, reverse result string.",
    "reverse: Walk E4 (for loop, two indices moving inward). Swap s[i] and s[j] until they meet.",
    "binary search: Walk E3 (while lo &lt;= hi). Compute mid, compare, adjust lo or hi.",
    "Read '(' then operand operator operand then ')'. Use getchar for parens, scanf for the rest.",
    "shellsort: Walk E4 (outer for with gap /= 2, inner for with insertion comparison). Gap starts at n/2.",
], start=1):
    E.append(Paragraph(f"<b>{i}.</b> {a}", styles['Body']))
    E.append(Spacer(1, 0.04*inch))

build_chapter_pdf("/mnt/user-data/outputs/Chapter_3_Control_Flow_Super.pdf", E)
print("Chapter 3 PDF generated successfully.")
