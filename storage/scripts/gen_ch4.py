#!/usr/bin/env python3
"""Generate Chapter 4: Functions and Program Structure Super PDF."""
import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
E = []

E.append(Paragraph("Chapter 4: Functions and Program Structure", styles['ChapterTitle']))
E.append(Paragraph("Relational graph edition. Pull-shaped. Top-down.", styles['Subtitle']))

E.append(Paragraph("How to use this document", styles['SectionHead']))
E.append(Paragraph("Red = checkpoint. Green = practice. Blue = target. Walk edges in order.", styles['Body']))

E.append(Paragraph("Big picture", styles['SectionHead']))
E.append(Paragraph(
    "Functions decompose programs into named, reusable operations. Scope rules determine which "
    "names are visible where. The preprocessor runs before compilation and does text substitution. "
    "This chapter turns you from someone who writes one big main() into someone who designs programs.",
    styles['Body']))

E.append(Paragraph("Target problem", styles['SectionHead']))
E.append(target_box([
    Paragraph("<b>Build a reverse-Polish calculator with: a main loop, separate push/pop functions "
              "for a value stack, a getop function that reads tokens, a #define for stack size, "
              "and a recursive factorial function for testing.</b>", styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.15*inch))

E.append(Paragraph("The Graph", styles['SectionHead']))
E.append(Paragraph("Node inventory", styles['SubHead']))
E.append(make_table(
    ["Node", "What it is", "Key rule"],
    [
        ["function definition", "return-type name(params) { body }", "The actual code that runs"],
        ["function declaration/prototype", "return-type name(params);", "Tells compiler the signature exists (no body)"],
        ["extern", "extern type name;", "Variable/function defined in another file"],
        ["static (file scope)", "static type name;", "Limits visibility to this file only"],
        ["static (local)", "static type name; inside function", "Value persists between calls"],
        ["register", "register type name;", "Hint: keep in CPU register (compiler may ignore)"],
        ["#include", "#include &lt;file&gt; or \"file\"", "Copies file contents into this position"],
        ["#define", "#define NAME(args) body", "Text substitution macro, can take arguments"],
        ["#ifdef / #ifndef", "#ifdef NAME ... #endif", "Conditional compilation"],
        ["recursion", "function calls itself", "Needs base case to terminate"],
    ],
    col_widths=[1.8*inch, 2.2*inch, 2.5*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Edge inventory", styles['SubHead']))
E.append(make_table(
    ["Edge", "Rule"],
    [
        ["E1: Scope", "A name is visible from its declaration to the end of its enclosing block/file"],
        ["E2: Linkage", "extern = visible across files. static = visible only in this file"],
        ["E3: Declaration before use", "Functions must be declared (prototype) before they are called"],
        ["E4: Preprocessor order", "#include runs first, then #define expands, then #ifdef evaluates, then compiler sees result"],
        ["E5: Call stack", "Each function call pushes a frame (locals + return address). Return pops it"],
        ["E6: Recursion", "Function calls itself with smaller input until base case returns directly"],
    ],
    col_widths=[1.8*inch, 4.7*inch]
))
E.append(PageBreak())

E.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

E.append(Paragraph("Edge 1: Function basics", styles['SubHead']))
E.append(Paragraph(
    "A function has four parts: return type, name, parameter list, body. "
    "If no return type is specified, old C assumed int (modern C requires explicit type). "
    "A function that returns nothing uses <font name='Courier'>void</font>.",
    styles['Body']))
E.append(Paragraph('/* Declaration (prototype) -- promises this function exists */', styles['Code']))
E.append(Paragraph('double celsius(int fahr);', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('/* Definition -- the actual implementation */', styles['Code']))
E.append(Paragraph('double celsius(int fahr) {', styles['Code']))
E.append(Paragraph('    return (5.0 / 9.0) * (fahr - 32);', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 4.1:</b> Write a function <font name='Courier'>int abs_val(int x)</font> "
              "that returns the absolute value of x.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>int abs_val(int x) { return (x &lt; 0) ? -x : x; }</font>",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 2: Scope -- where names live", styles['SubHead']))
E.append(Paragraph(
    "Local variables: visible only inside their function. Die when function returns. "
    "External variables: defined outside all functions. Visible from definition to end of file. "
    "Block variables: declared inside { }, visible only within that block.",
    styles['Body']))
E.append(concept_box([
    Paragraph("<b>Scope hierarchy:</b> block &sub; function &sub; file &sub; program", styles['CalloutText']),
    Paragraph("Inner scopes can shadow outer names. Avoid this -- it creates confusion.", styles['CalloutText']),
], styles))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 4.2:</b> In the code below, what value does each printf output?", styles['CalloutText']),
    Paragraph("<font name='Courier'>int x = 1; { int x = 2; printf(\"%d\", x); } printf(\"%d\", x);</font>",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> 2 then 1. The inner x shadows the outer x inside the block.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 3: static -- controlling visibility and lifetime", styles['SubHead']))
E.append(Paragraph(
    "Two uses of static: (1) On an external variable or function: limits visibility to this file "
    "(other files cannot see it even with extern). (2) On a local variable: value persists between "
    "calls (not destroyed when function returns).",
    styles['Body']))
E.append(Paragraph('/* File-scope static: invisible to other files */', styles['Code']))
E.append(Paragraph('static int module_counter = 0;', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('/* Local static: persists between calls */', styles['Code']))
E.append(Paragraph('int next_id(void) {', styles['Code']))
E.append(Paragraph('    static int id = 0;', styles['Code']))
E.append(Paragraph('    return ++id;  /* returns 1, 2, 3, ... on successive calls */', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 4.3:</b> What does next_id() return on its 5th call?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> 5. The static local id persists and increments each call.", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Without looking: what is the difference between a declaration and a definition? "
              "What are the two meanings of static? What is extern for?", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Edge 4: Header files and program organization", styles['SubHead']))
E.append(Paragraph(
    "Put shared declarations (prototypes, #defines, type definitions) in .h files. "
    "Put implementations in .c files. Each .c file #includes the headers it needs. "
    "This prevents duplication and ensures all files agree on function signatures.",
    styles['Body']))
E.append(Paragraph('/* calc.h -- shared declarations */', styles['Code']))
E.append(Paragraph('#define MAXVAL 100', styles['Code']))
E.append(Paragraph('void push(double);', styles['Code']))
E.append(Paragraph('double pop(void);', styles['Code']))
E.append(Paragraph('int getop(char []);', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 4.4:</b> Why should you NOT put variable definitions (like <font name='Courier'>int x = 5;</font>) "
              "in a header file?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> Every .c file that #includes the header gets its own copy of x, "
              "causing linker errors (multiple definitions of the same symbol).", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 5: The C preprocessor", styles['SubHead']))
E.append(Paragraph(
    "Runs BEFORE the compiler. Three key operations: "
    "(1) #include copies file contents in. "
    "(2) #define does text replacement (can take arguments). "
    "(3) #ifdef/#ifndef enables conditional compilation.",
    styles['Body']))
E.append(Paragraph('#define MAX(a, b) ((a) > (b) ? (a) : (b))', styles['Code']))
E.append(Paragraph('#define SQUARE(x) ((x) * (x))', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('/* Parentheses are critical! Without them: */', styles['Code']))
E.append(Paragraph('/* SQUARE(1+2) becomes ((1+2) * (1+2)) = 9  (correct) */', styles['Code']))
E.append(Paragraph('/* Without parens: 1+2 * 1+2 = 5  (wrong!) */', styles['Code']))
E.append(Paragraph(
    "<b>Macro trap:</b> Arguments are substituted textually, not evaluated. "
    "Always parenthesize every parameter and the whole expression.", styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 4.5:</b> What does <font name='Courier'>SQUARE(i++)</font> expand to? Why is this a problem?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>((i++) * (i++))</font> -- i is incremented twice! "
              "Side effects in macro arguments are dangerous.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 6: Recursion", styles['SubHead']))
E.append(Paragraph(
    "A function calls itself with a smaller input until reaching a base case. "
    "Each call pushes a new stack frame. The base case returns without recursing, "
    "and the stack unwinds.", styles['Body']))
E.append(Paragraph('long factorial(int n) {', styles['Code']))
E.append(Paragraph('    if (n &lt;= 1) return 1;         /* base case */', styles['Code']))
E.append(Paragraph('    return n * factorial(n - 1);   /* recursive case */', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 4.6:</b> Trace factorial(4): how many stack frames are created? What value does each return?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> 4 frames. factorial(1)=1, factorial(2)=2, factorial(3)=6, factorial(4)=24.", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("Write a recursive function <font name='Courier'>void printd(int n)</font> that prints "
              "an integer as a string of digits. (Hint: print the leading digits recursively, then the last digit.)",
              styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── SOLVE TARGET ──
E.append(Paragraph("Solving the target problem", styles['SectionHead']))
E.append(Paragraph("Walk: E4 (header with shared declarations) + E1 (push/pop/getop functions) + E2 (scope for stack) + E6 (factorial for testing)", styles['Body']))
E.append(Spacer(1, 0.05*inch))

E.append(Paragraph('/* Simplified RPN calculator */', styles['Code']))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('#include &lt;stdlib.h&gt;  /* for atof */', styles['Code']))
E.append(Paragraph('#include &lt;ctype.h&gt;', styles['Code']))
E.append(Paragraph('#define MAXOP 100', styles['Code']))
E.append(Paragraph('#define NUMBER 0', styles['Code']))
E.append(Paragraph('#define MAXVAL 100', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('static int sp = 0;              /* stack pointer */', styles['Code']))
E.append(Paragraph('static double val[MAXVAL];      /* value stack */', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('void push(double f) {', styles['Code']))
E.append(Paragraph('    if (sp &lt; MAXVAL) val[sp++] = f;', styles['Code']))
E.append(Paragraph('    else printf("error: stack full\\n");', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('double pop(void) {', styles['Code']))
E.append(Paragraph('    if (sp > 0) return val[--sp];', styles['Code']))
E.append(Paragraph('    printf("error: stack empty\\n");', styles['Code']))
E.append(Paragraph('    return 0.0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph(
    "<b>Edges walked:</b> E2 (static file-scope for sp and val -- hidden from other files), "
    "E1 (push/pop as separate functions), E5 (#define for constants).",
    styles['BoldBody']))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("Add a <font name='Courier'>long factorial(int n)</font> recursive function to the calculator. "
              "When the user types 'f', pop the top value, compute its factorial, and push the result. "
              "Report your code.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── FINAL EXAM ──
E.append(Paragraph("Final exam", styles['SectionHead']))
for i, q in enumerate([
    "Add modulus (%) support to the RPN calculator for integer operands.",
    "Write a macro <font name='Courier'>#define SWAP(t,x,y)</font> that swaps two variables of type t.",
    "Add an 'undo' command to the calculator that restores the stack to its state before the last operation.",
    "Rewrite printd (recursive digit printer) to handle the most negative integer correctly.",
    "Write a <font name='Courier'>#ifndef</font> include guard for calc.h and explain why it is needed.",
], start=1):
    E.append(KeepTogether([problem_box([
        Paragraph(f"<b>Problem {i}:</b> {q}", styles['CalloutText']),
    ], styles)]))
    E.append(Spacer(1, 0.05*inch))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT D", styles['CheckpointText']),
    Paragraph("Solve at least 3. Report in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Answer key", styles['SectionHead']))
for i, a in enumerate([
    "Walk E1: pop two values, cast to int, compute a%b, push result. Handle b==0.",
    "#define SWAP(t,x,y) do { t _tmp = (x); (x) = (y); (y) = _tmp; } while(0). The do-while(0) makes it safe in if-else.",
    "Walk E3: save sp to a static prev_sp before each operation. 'undo' restores sp = prev_sp.",
    "Walk E6: INT_MIN cannot be negated (overflow). Handle it specially by printing the last digit of the positive remainder.",
    "#ifndef CALC_H / #define CALC_H / ... / #endif. Without it, including calc.h twice causes duplicate declarations.",
], start=1):
    E.append(Paragraph(f"<b>{i}.</b> {a}", styles['Body']))
    E.append(Spacer(1, 0.04*inch))

build_chapter_pdf("/mnt/user-data/outputs/Chapter_4_Functions_Super.pdf", E)
print("Chapter 4 PDF generated successfully.")
