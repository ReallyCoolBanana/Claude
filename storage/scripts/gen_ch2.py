#!/usr/bin/env python3
"""Generate Chapter 2: Types, Operators, and Expressions Super PDF."""
import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
E = []

E.append(Paragraph("Chapter 2: Types, Operators, and Expressions", styles['ChapterTitle']))
E.append(Paragraph("Relational graph edition. Pull-shaped. Top-down.", styles['Subtitle']))

E.append(Paragraph("How to use this document", styles['SectionHead']))
E.append(Paragraph(
    "Red boxes = checkpoints (answer in chat). Green boxes = practice with inline answers. "
    "Blue box = target problem. Walk the edges in order.", styles['Body']))

E.append(Paragraph("Big picture", styles['SectionHead']))
E.append(Paragraph(
    "Every value in C has a type. Types determine how many bytes are used, what operations are legal, "
    "and what happens when you mix types in an expression. Operators transform values. "
    "This chapter is the type system and operator reference -- the rules of the road.", styles['Body']))

E.append(Paragraph("Target problem", styles['SectionHead']))
E.append(target_box([
    Paragraph("<b>Write a function <font name='Courier'>unsigned int bitcount(unsigned int x)</font> "
              "that counts the number of 1-bits in x using bitwise operators. "
              "Then write a test that demonstrates type conversion by printing the count as int, float, and char.</b>",
              styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.15*inch))

# ── GRAPH ──
E.append(Paragraph("The Graph", styles['SectionHead']))
E.append(Paragraph("Node inventory", styles['SubHead']))
E.append(make_table(
    ["Node", "Size (typical)", "Range / Purpose"],
    [
        ["char", "1 byte", "0-255 unsigned, or -128 to 127 signed"],
        ["int", "4 bytes", "-2B to +2B (32 bits)"],
        ["long", "8 bytes", "At least 32 bits, often 64"],
        ["float", "4 bytes", "~7 decimal digits precision"],
        ["double", "8 bytes", "~15 decimal digits precision"],
        ["unsigned", "modifier", "No sign bit -- doubles positive range"],
        ["enum", "named ints", "enum bool { NO, YES }; -- NO=0, YES=1"],
        ["const", "modifier", "Value cannot be changed after init"],
    ],
    col_widths=[1.2*inch, 1.2*inch, 4.1*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Operator edge table", styles['SubHead']))
E.append(make_table(
    ["Category", "Operators", "Key rule"],
    [
        ["Arithmetic", "+ - * / %", "Integer / truncates. % is remainder (ints only)"],
        ["Relational", "&gt; &lt; &gt;= &lt;= == !=", "Result is int: 1 (true) or 0 (false)"],
        ["Logical", "&amp;&amp; || !", "Short-circuit: stops evaluating when result is known"],
        ["Bitwise", "&amp; | ^ ~ &lt;&lt; &gt;&gt;", "Operate on individual bits. &amp; masks, | sets, ^ toggles, ~ inverts"],
        ["Assignment", "= += -= *= /= %= &amp;= |= ^= &lt;&lt;= &gt;&gt;=", "x op= y means x = x op y"],
        ["Increment", "++x x++ --x x--", "Prefix: increment then use. Postfix: use then increment"],
        ["Ternary", "a ? b : c", "If a is nonzero, result is b; else c"],
    ],
    col_widths=[1.1*inch, 2.0*inch, 3.4*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Implicit conversion chain (memorize this)", styles['SubHead']))
E.append(concept_box([
    Paragraph("<b>char &rarr; int &rarr; long &rarr; float &rarr; double</b>", styles['CalloutText']),
    Paragraph("When two operands differ in type, the narrower one is promoted to the wider one. "
              "This happens silently. Assigning a wider type to a narrower type truncates (may lose data).",
              styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── ZOOM IN ──
E.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

E.append(Paragraph("Edge 1: Data types and sizes", styles['SubHead']))
E.append(Paragraph(
    "Use <font name='Courier'>sizeof(type)</font> to check actual size on your machine. "
    "The only guarantees: <font name='Courier'>sizeof(char) == 1</font>, "
    "<font name='Courier'>sizeof(short) &lt;= sizeof(int) &lt;= sizeof(long)</font>. "
    "Header <font name='Courier'>&lt;limits.h&gt;</font> defines exact ranges (INT_MAX, CHAR_BIT, etc).",
    styles['Body']))
E.append(Paragraph('printf("int: %zu bytes, max: %d\\n", sizeof(int), INT_MAX);', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.1:</b> Write a program that prints the size of char, int, long, float, and double on your machine.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> Five printf calls using <font name='Courier'>sizeof(type)</font> with %zu format.",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 2: Constants -- all the forms", styles['SubHead']))
E.append(make_table(
    ["Form", "Example", "Type"],
    [
        ["Decimal", "42", "int"],
        ["Octal", "052", "int (leading 0)"],
        ["Hex", "0x2A", "int (leading 0x)"],
        ["Character", "'*'", "int (ASCII value 42)"],
        ["String", "\"hello\"", "char array + \\0"],
        ["Float", "3.14", "double (not float!)"],
        ["Float (explicit)", "3.14f", "float"],
        ["Long", "42L", "long"],
        ["Unsigned", "42U", "unsigned int"],
        ["Enum", "enum { RED, GREEN=5, BLUE }", "RED=0, GREEN=5, BLUE=6"],
    ],
    col_widths=[1.3*inch, 2.5*inch, 2.7*inch]
))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.2:</b> What type is each: 077, 0xFF, 'A', 3.14, 3.14f, 100L?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> int(octal 63), int(hex 255), int(65), double, float, long.",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 3: Arithmetic operators", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>%</font> (modulus) works only on integers. "
    "<font name='Courier'>/</font> truncates when both operands are integers: "
    "<font name='Courier'>7/2 == 3</font>, not 3.5. "
    "To get float division, make at least one operand float: <font name='Courier'>7.0/2 == 3.5</font>.",
    styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.3:</b> What is the value of: 13/5, 13%5, 13.0/5?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> 2, 3, 2.6", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 4: Relational, logical, short-circuit", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>&amp;&amp;</font> and <font name='Courier'>||</font> short-circuit: "
    "in <font name='Courier'>a &amp;&amp; b</font>, if a is 0 then b is never evaluated. "
    "In <font name='Courier'>a || b</font>, if a is nonzero then b is never evaluated. "
    "This is a guarantee, not an optimization. You can rely on it.",
    styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.4:</b> In <font name='Courier'>if (p != NULL &amp;&amp; *p == 'x')</font>, "
              "why is the order of the two tests critical?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> Short-circuit. If p is NULL, the second test (*p) is never evaluated, "
              "avoiding a null pointer dereference.", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Without looking above: what is the implicit promotion chain? "
              "What does 7/2 evaluate to, and why? What does short-circuit mean?", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Edge 5: Type conversions", styles['SubHead']))
E.append(Paragraph(
    "Implicit: narrower type auto-promotes to wider in mixed expressions. "
    "Explicit: <font name='Courier'>(type)expr</font> forces conversion. "
    "Assigning double to int truncates toward zero. Assigning negative to unsigned wraps around.",
    styles['Body']))
E.append(Paragraph('int n = 3;', styles['Code']))
E.append(Paragraph('double avg = (double)n / 2;  /* cast n to double first, then divide */', styles['Code']))
E.append(Paragraph('int trunc = (int)3.99;       /* trunc == 3, fractional part dropped */', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.5:</b> What does <font name='Courier'>unsigned u = -1;</font> produce?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> UINT_MAX (all bits set to 1). Negative values wrap around in unsigned.",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 6: Increment/decrement -- prefix vs postfix", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>++i</font>: increment i, then use the new value. "
    "<font name='Courier'>i++</font>: use the current value of i, then increment. "
    "In standalone statements (<font name='Courier'>i++;</font>) there is no difference. "
    "In expressions, it matters.",
    styles['Body']))
E.append(Paragraph('int i = 5;', styles['Code']))
E.append(Paragraph('int a = ++i;  /* i becomes 6, a gets 6 */', styles['Code']))
E.append(Paragraph('int b = i++;  /* b gets 6 (current i), then i becomes 7 */', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.6:</b> If <font name='Courier'>int x = 3;</font>, what is the value of "
              "<font name='Courier'>x++ + ++x</font>?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> Undefined behavior. Never modify the same variable twice in one expression "
              "without a sequence point.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 7: Bitwise operators", styles['SubHead']))
E.append(make_table(
    ["Operator", "Name", "What it does"],
    [
        ["&amp;", "AND", "Both bits must be 1 to produce 1"],
        ["|", "OR", "Either bit being 1 produces 1"],
        ["^", "XOR", "Exactly one bit being 1 produces 1"],
        ["~", "NOT", "Flips every bit"],
        ["&lt;&lt;", "Left shift", "Shifts bits left, fills with 0s (multiplies by 2)"],
        ["&gt;&gt;", "Right shift", "Shifts bits right (divides by 2, sign-extension varies)"],
    ],
    col_widths=[1.0*inch, 1.2*inch, 4.3*inch]
))
E.append(Paragraph(
    "<b>Common pattern -- masking:</b> <font name='Courier'>x &amp; 0xFF</font> extracts the lowest 8 bits. "
    "<font name='Courier'>x | 0x80</font> sets bit 7. <font name='Courier'>x &amp; ~0x80</font> clears bit 7.",
    styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.7:</b> Write an expression that extracts bits 4-7 (4 bits) from an unsigned int x.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>(x &gt;&gt; 4) &amp; 0xF</font> -- shift right 4 to align, then mask.",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 8: Precedence (the trap)", styles['SubHead']))
E.append(Paragraph(
    "When in doubt, use parentheses. The most common trap: "
    "<font name='Courier'>&amp;</font> and <font name='Courier'>|</font> have LOWER precedence than "
    "<font name='Courier'>==</font>. So <font name='Courier'>x &amp; mask == 0</font> is parsed as "
    "<font name='Courier'>x &amp; (mask == 0)</font>, which is almost certainly wrong. "
    "Write <font name='Courier'>(x &amp; mask) == 0</font> instead.",
    styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 2.8:</b> What does <font name='Courier'>2 &lt;&lt; 3 + 1</font> evaluate to?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>2 &lt;&lt; (3+1) = 2 &lt;&lt; 4 = 32</font>. "
              "+ has higher precedence than &lt;&lt;.", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("Write a function <font name='Courier'>unsigned rightrot(unsigned x, int n)</font> "
              "that returns x rotated right by n bit positions. Bits that fall off the right end "
              "reappear on the left.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── SOLVE TARGET ──
E.append(Paragraph("Solving the target problem", styles['SectionHead']))
E.append(Paragraph("Walk: E7 (bitwise) + E5 (type conversion) + E6 (increment)", styles['Body']))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('unsigned int bitcount(unsigned int x) {', styles['Code']))
E.append(Paragraph('    unsigned int count = 0;', styles['Code']))
E.append(Paragraph('    for (; x != 0; x &gt;&gt;= 1)  /* E7: shift right */', styles['Code']))
E.append(Paragraph('        count += x &amp; 1;        /* E7: mask lowest bit */', styles['Code']))
E.append(Paragraph('    return count;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int main(void) {', styles['Code']))
E.append(Paragraph('    unsigned int val = 0xAB;  /* binary: 10101011, expect 5 */', styles['Code']))
E.append(Paragraph('    unsigned int n = bitcount(val);', styles['Code']))
E.append(Paragraph('    printf("int:   %u\\n", n);              /* E5: no conversion */', styles['Code']))
E.append(Paragraph('    printf("float: %.1f\\n", (double)n);    /* E5: explicit cast */', styles['Code']))
E.append(Paragraph("    printf(\"char:  '%c'\\n\", (char)('0'+n)); /* E5: int to char */", styles['Code']))
E.append(Paragraph('    return 0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))

E.append(Spacer(1, 0.1*inch))
E.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("Rewrite bitcount using the trick: <font name='Courier'>x &amp;= (x-1)</font> "
              "clears the rightmost 1-bit. Why is this faster?", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── FINAL EXAM ──
E.append(Paragraph("Final exam", styles['SectionHead']))
for i, q in enumerate([
    "Write a function <font name='Courier'>int htoi(char s[])</font> that converts a hex string to int.",
    "Write a function <font name='Courier'>unsigned invert(unsigned x, int p, int n)</font> that inverts n bits starting at position p.",
    "Write a function <font name='Courier'>unsigned setbits(unsigned x, int p, int n, unsigned y)</font> that sets n bits of x starting at p to the rightmost n bits of y.",
    "Write an expression equivalent to <font name='Courier'>x *= (1 &lt;&lt; n)</font> using only shift operators.",
    "Explain why <font name='Courier'>strlen(s) - strlen(t)</font> can produce a wrong result when s is shorter than t, and how to fix it.",
], start=1):
    E.append(KeepTogether([problem_box([
        Paragraph(f"<b>Problem {i}:</b> {q}", styles['CalloutText']),
    ], styles)]))
    E.append(Spacer(1, 0.05*inch))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT D", styles['CheckpointText']),
    Paragraph("Solve at least 3 of the 5 problems. Report in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Answer key", styles['SectionHead']))
for i, a in enumerate([
    "htoi: Walk E2 (hex constants) + E5 (char to int). Loop through chars, multiply accumulator by 16, add digit value. 'a'-'f' = 10-15.",
    "invert: Walk E7. Create mask of n bits at position p with shifts, XOR with x.",
    "setbits: Walk E7. Clear target bits with AND+NOT mask, OR in the new bits shifted to position.",
    "x &lt;&lt;= n. Left shift by n is multiplication by 2^n.",
    "strlen returns size_t (unsigned). If s is shorter, the subtraction wraps to a huge positive number. Fix: cast to int, or compare lengths before subtracting.",
], start=1):
    E.append(Paragraph(f"<b>{i}.</b> {a}", styles['Body']))
    E.append(Spacer(1, 0.04*inch))

build_chapter_pdf("/mnt/user-data/outputs/Chapter_2_Types_Operators_Super.pdf", E)
print("Chapter 2 PDF generated successfully.")
