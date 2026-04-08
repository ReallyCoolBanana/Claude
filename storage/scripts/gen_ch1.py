#!/usr/bin/env python3
"""Generate Chapter 1: A Tutorial Introduction Super PDF."""
import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
E = []  # elements

# ── TITLE ──
E.append(Paragraph("Chapter 1: A Tutorial Introduction", styles['ChapterTitle']))
E.append(Paragraph("Relational graph edition. Pull-shaped. Top-down.", styles['Subtitle']))
E.append(Spacer(1, 0.1*inch))

# ── HOW TO USE ──
E.append(Paragraph("How to use this document", styles['SectionHead']))
E.append(Paragraph(
    "Red boxes are checkpoints. Stop and answer them in chat before continuing. "
    "Green boxes are practice problems with inline answers. "
    "Blue box is the target problem you will solve by the end. "
    "Read the graph first, then walk the edges one at a time.", styles['Body']))

# ── BIG PICTURE ──
E.append(Paragraph("Big picture", styles['SectionHead']))
E.append(Paragraph(
    "This chapter bootstraps you from zero to writing real C programs. "
    "A C program is a set of functions. One function is called <font name='Courier'>main</font> -- "
    "the OS calls it when your program starts. Functions call other functions, pass data via arguments, "
    "and return results. Every concept below is a tool you need to solve the target problem.",
    styles['Body']))

# ── TARGET ──
E.append(Paragraph("Target problem", styles['SectionHead']))
E.append(target_box([
    Paragraph("<b>Write a program that reads lines from stdin and prints the longest one.</b> "
              "It must use: a main function, a getline function, a copy function, "
              "character arrays, and an external variable for the max length seen so far.",
              styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.15*inch))

# ── THE GRAPH ──
E.append(Paragraph("The Graph", styles['SectionHead']))
E.append(Paragraph("Node inventory -- memorize these", styles['SubHead']))
E.append(make_table(
    ["Node", "What it is", "Why it matters"],
    [
        ["main()", "Entry point function", "OS calls this to start your program"],
        ["printf(fmt, ...)", "Formatted output to stdout", "Primary way to print results"],
        ["getchar()", "Read one character from stdin", "Returns int (char value or EOF)"],
        ["putchar(c)", "Write one character to stdout", "Outputs a single byte"],
        ["EOF", "End-of-file sentinel (usually -1)", "Signals no more input"],
        ["int, float, char", "Basic data types", "int: integers, float: decimals, char: single bytes"],
        ["for / while", "Loop constructs", "Repeat a block until condition is false"],
        ["#define NAME val", "Symbolic constant", "Preprocessor replaces NAME with val everywhere"],
        ["type name[SIZE]", "Array declaration", "Contiguous block of SIZE elements"],
        ["return-type fn(params)", "Function definition", "Named, reusable block of code"],
        ["extern", "External variable declaration", "Variable defined in another file/scope"],
    ],
    col_widths=[1.5*inch, 2.0*inch, 3.0*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Edge inventory -- how nodes connect", styles['SubHead']))
E.append(make_table(
    ["Edge", "From", "To", "Rule"],
    [
        ["E1", "main()", "printf()", "main calls printf to produce output"],
        ["E2", "variables", "printf()", "Format specifiers (%d, %f) insert variable values"],
        ["E3", "for/while", "statements", "Loop body executes while condition is true"],
        ["E4", "#define", "any code", "Preprocessor substitutes name with value before compilation"],
        ["E5", "getchar()", "variable", "Returns next char (as int); EOF when input ends"],
        ["E6", "array[i]", "value", "Index i selects one element from the block"],
        ["E7", "function()", "return value", "Function executes body, returns a value to caller"],
        ["E8", "extern", "variable", "Declares a variable defined elsewhere is accessible here"],
    ],
    col_widths=[0.5*inch, 1.3*inch, 1.3*inch, 3.4*inch]
))
E.append(PageBreak())

# ── ZOOM IN ──
E.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

# Edge 1: hello world
E.append(Paragraph("Edge 1: main() calls printf()", styles['SubHead']))
E.append(Paragraph(
    "Every C program must have exactly one function called <font name='Courier'>main</font>. "
    "The OS calls it. <font name='Courier'>printf</font> is a library function that writes "
    "formatted text to stdout. The <font name='Courier'>\\n</font> in the format string is a newline character.",
    styles['Body']))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int main(void) {', styles['Code']))
E.append(Paragraph('    printf("hello, world\\n");', styles['Code']))
E.append(Paragraph('    return 0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Spacer(1, 0.08*inch))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.1:</b> Write a program that prints your name on one line "
              "and your age on the next line.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>printf(\"Alice\\n\"); printf(\"Age: 30\\n\");</font> "
              "inside main. Two printf calls, each ending with \\n.", styles['CalloutText']),
], styles)]))

# Edge 2: Variables + formatting
E.append(Paragraph("Edge 2: Variables feed into printf() via format specifiers", styles['SubHead']))
E.append(Paragraph(
    "Declare a variable with <font name='Courier'>type name;</font> or "
    "<font name='Courier'>type name = value;</font>. "
    "In printf, <font name='Courier'>%d</font> prints an int, "
    "<font name='Courier'>%f</font> prints a float, "
    "<font name='Courier'>%c</font> prints a char.",
    styles['Body']))
E.append(Paragraph('int fahr = 100;', styles['Code']))
E.append(Paragraph('float celsius = (5.0/9.0) * (fahr - 32);', styles['Code']))
E.append(Paragraph('printf("%d F = %.1f C\\n", fahr, celsius);', styles['Code']))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph(
    "<b>Key rule:</b> Integer division truncates. <font name='Courier'>5/9</font> is 0. "
    "Write <font name='Courier'>5.0/9.0</font> to get floating-point division.",
    styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.2:</b> Write a program that converts 212 F to Celsius and prints the result "
              "with one decimal place.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>float c = (5.0/9.0)*(212-32); printf(\"%.1f\\n\", c);</font> "
              "Output: 100.0", styles['CalloutText']),
], styles)]))

# Edge 3: for loop
E.append(Paragraph("Edge 3: for/while loops repeat a block", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>for (init; test; increment) { body }</font> -- "
    "init runs once, then: test, body, increment, repeat. "
    "Equivalent to while but keeps loop machinery on one line.",
    styles['Body']))
E.append(Paragraph('for (int fahr = 0; fahr &lt;= 300; fahr += 20) {', styles['Code']))
E.append(Paragraph('    printf("%3d %6.1f\\n", fahr, (5.0/9.0)*(fahr-32));', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.3:</b> Write a for loop that prints the numbers 1 through 10, one per line.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>for (int i = 1; i &lt;= 10; i++) printf(\"%d\\n\", i);</font>",
              styles['CalloutText']),
], styles)]))

# Edge 4: #define
E.append(Paragraph("Edge 4: #define replaces magic numbers with names", styles['SubHead']))
E.append(Paragraph(
    "The preprocessor replaces every occurrence of the name with the value before the compiler sees it. "
    "No semicolon. No equals sign. Just <font name='Courier'>#define NAME value</font>.",
    styles['Body']))
E.append(Paragraph('#define LOWER 0', styles['Code']))
E.append(Paragraph('#define UPPER 300', styles['Code']))
E.append(Paragraph('#define STEP  20', styles['Code']))
E.append(Paragraph('for (int f = LOWER; f &lt;= UPPER; f += STEP)', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.4:</b> Replace the magic numbers 1 and 10 in your Problem 1.3 answer with #define constants.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>#define START 1</font> and "
              "<font name='Courier'>#define END 10</font>, then use them in the for loop.",
              styles['CalloutText']),
], styles)]))

# ── CHECKPOINT A ──
E.append(Spacer(1, 0.1*inch))
E.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Before continuing, write a complete program (with #include, main, and a for loop) "
              "that prints a Celsius-to-Fahrenheit table from 0 to 100 in steps of 10. "
              "Use #define for the bounds. Report your code in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# Edge 5: getchar/putchar/EOF
E.append(Paragraph("Edge 5: getchar()/putchar() -- character-at-a-time I/O", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>getchar()</font> returns the next character from stdin as an int. "
    "When input ends, it returns <font name='Courier'>EOF</font> (defined in stdio.h, usually -1). "
    "Must store in <font name='Courier'>int</font>, not <font name='Courier'>char</font>, "
    "because EOF must be distinguishable from any valid char value.",
    styles['Body']))
E.append(Paragraph('int c;', styles['Code']))
E.append(Paragraph('while ((c = getchar()) != EOF)', styles['Code']))
E.append(Paragraph('    putchar(c);', styles['Code']))
E.append(Paragraph(
    "This copies stdin to stdout, one character at a time, until EOF.", styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.5:</b> Write a program that counts the number of characters in its input.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>long count = 0; int c; "
              "while ((c = getchar()) != EOF) ++count; printf(\"%ld\\n\", count);</font>",
              styles['CalloutText']),
], styles)]))

# Edge 6: Arrays
E.append(Paragraph("Edge 6: Arrays -- indexed blocks of same-type values", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>int counts[10];</font> declares 10 ints, indexed 0 through 9. "
    "Array indices start at 0. Accessing beyond bounds is undefined behavior (no safety net).",
    styles['Body']))
E.append(Paragraph('int digit_count[10];', styles['Code']))
E.append(Paragraph('for (int i = 0; i &lt; 10; i++)', styles['Code']))
E.append(Paragraph('    digit_count[i] = 0;', styles['Code']))
E.append(Paragraph('int c;', styles['Code']))
E.append(Paragraph('while ((c = getchar()) != EOF)', styles['Code']))
E.append(Paragraph("    if (c >= '0' &amp;&amp; c &lt;= '9')", styles['Code']))
E.append(Paragraph("        ++digit_count[c - '0'];", styles['Code']))
E.append(Paragraph(
    "<b>Key trick:</b> <font name='Courier'>c - '0'</font> converts a digit character to its numeric value. "
    "'5' - '0' = 5. Works because digit characters are contiguous in ASCII.",
    styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.6:</b> Write a program that counts how many times each lowercase vowel "
              "(a, e, i, o, u) appears in its input.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> Use an array of 5 ints. Use if/else-if or switch on each char to decide "
              "which counter to increment.", styles['CalloutText']),
], styles)]))

# ── CHECKPOINT B ──
E.append(Spacer(1, 0.1*inch))
E.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("Write a word-counting program from scratch. A word is any sequence of characters "
              "that does not contain a space, tab, or newline. Count words, lines, and characters. "
              "Report your code in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# Edge 7: Functions
E.append(Paragraph("Edge 7: Functions -- named reusable blocks", styles['SubHead']))
E.append(Paragraph(
    "A function has: return type, name, parameter list, body. "
    "Call it by name with arguments. It returns a value (or void for no value). "
    "Functions must be declared before use (prototype or definition).",
    styles['Body']))
E.append(Paragraph('int power(int base, int exp) {', styles['Code']))
E.append(Paragraph('    int result = 1;', styles['Code']))
E.append(Paragraph('    for (int i = 0; i &lt; exp; i++)', styles['Code']))
E.append(Paragraph('        result *= base;', styles['Code']))
E.append(Paragraph('    return result;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph('/* In main: */', styles['Code']))
E.append(Paragraph('printf("2^10 = %d\\n", power(2, 10));', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.7:</b> Write a function <font name='Courier'>int max(int a, int b)</font> "
              "that returns the larger of two integers. Call it from main.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>int max(int a, int b) { if (a > b) return a; return b; }</font>",
              styles['CalloutText']),
], styles)]))

# Edge 8: Call by value
E.append(Paragraph("Edge 8: Call by value -- functions get copies", styles['SubHead']))
E.append(Paragraph(
    "C passes all arguments by value. The function receives a copy of each argument. "
    "Modifying the copy inside the function does NOT change the original in the caller. "
    "This is different from arrays -- when you pass an array name, you pass the address of element 0.",
    styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.8:</b> If you write <font name='Courier'>void inc(int x) { x++; }</font> "
              "and call <font name='Courier'>inc(n)</font>, does n change?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> No. The function increments its local copy. n is unchanged.",
              styles['CalloutText']),
], styles)]))

# Edge 9: Character arrays / strings
E.append(Paragraph("Edge 9: Character arrays -- strings end with \\0", styles['SubHead']))
E.append(Paragraph(
    "A string in C is a character array terminated by <font name='Courier'>'\\0'</font> (null character, value 0). "
    "String literal <font name='Courier'>\"hello\"</font> is stored as: h, e, l, l, o, \\0 -- 6 bytes total. "
    "The \\0 tells functions like printf where the string ends.",
    styles['Body']))
E.append(Paragraph('int getline(char s[], int lim) {', styles['Code']))
E.append(Paragraph('    int c, i;', styles['Code']))
E.append(Paragraph('    for (i = 0; i &lt; lim-1 &amp;&amp; (c=getchar()) != EOF &amp;&amp; c != \'\\n\'; i++)', styles['Code']))
E.append(Paragraph('        s[i] = c;', styles['Code']))
E.append(Paragraph("    if (c == '\\n') s[i++] = c;", styles['Code']))
E.append(Paragraph("    s[i] = '\\0';", styles['Code']))
E.append(Paragraph('    return i;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.9:</b> Write a function <font name='Courier'>void copy(char to[], char from[])</font> "
              "that copies string from into to, including the \\0.",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>int i = 0; while ((to[i] = from[i]) != '\\0') i++;</font>",
              styles['CalloutText']),
], styles)]))

# Edge 10: extern
E.append(Paragraph("Edge 10: extern -- sharing variables across functions", styles['SubHead']))
E.append(Paragraph(
    "A variable defined outside all functions is an external (global) variable. "
    "Any function in the same file can access it by name. "
    "Functions in OTHER files use <font name='Courier'>extern int varname;</font> to declare access. "
    "External variables persist for the entire program lifetime. Use sparingly -- they create hidden dependencies.",
    styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 1.10:</b> What is the difference between a definition and a declaration of an external variable?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> Definition allocates storage: <font name='Courier'>int max_len;</font> (outside functions). "
              "Declaration announces existence: <font name='Courier'>extern int max_len;</font> (no storage created).",
              styles['CalloutText']),
], styles)]))
E.append(PageBreak())

# ── SOLVING THE TARGET ──
E.append(Paragraph("Solving the target problem", styles['SectionHead']))
E.append(Paragraph("Walk: E9 (getline with char array) + E7 (functions) + E10 (extern) + E6 (array comparison)", styles['Body']))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('#define MAXLINE 1000', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int my_getline(char line[], int maxline);', styles['Code']))
E.append(Paragraph('void copy(char to[], char from[]);', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int main(void) {', styles['Code']))
E.append(Paragraph('    int len, max;', styles['Code']))
E.append(Paragraph('    char line[MAXLINE], longest[MAXLINE];', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('    max = 0;', styles['Code']))
E.append(Paragraph('    while ((len = my_getline(line, MAXLINE)) > 0)', styles['Code']))
E.append(Paragraph('        if (len > max) {', styles['Code']))
E.append(Paragraph('            max = len;', styles['Code']))
E.append(Paragraph('            copy(longest, line);', styles['Code']))
E.append(Paragraph('        }', styles['Code']))
E.append(Paragraph('    if (max > 0)', styles['Code']))
E.append(Paragraph('        printf("%s", longest);', styles['Code']))
E.append(Paragraph('    return 0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Spacer(1, 0.08*inch))
E.append(Paragraph(
    "<b>Edges walked:</b> E4 (#define MAXLINE), E6 (char arrays line[] and longest[]), "
    "E7 (functions my_getline and copy), E3 (while loop over input lines), "
    "E9 (string termination with \\0 in getline), E2 (printf to output the result).",
    styles['BoldBody']))

# ── CHECKPOINT C ──
E.append(Spacer(1, 0.1*inch))
E.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("Modify the longest-line program to print ALL lines longer than 80 characters "
              "(not just the longest). Report your modified main() in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── FINAL EXAM ──
E.append(Paragraph("Final exam", styles['SectionHead']))

for i, (q, a) in enumerate([
    ("Write a program that prints all input lines in reverse order (last character first).",
     "Walk E5 + E9: read into char array with getline, then loop from len-1 down to 0 calling putchar."),
    ("Write a function that removes trailing blanks and tabs from a string.",
     "Walk E9 + E6: find the \\0, scan backwards past spaces/tabs, place new \\0."),
    ("Write a program that replaces each tab with the right number of spaces to reach the next tab stop (every 8 columns).",
     "Walk E5 + E3: track column count, on tab output spaces until column % 8 == 0."),
    ("Write a program that folds long input lines into two or more shorter lines after the last blank before column 80.",
     "Walk E5 + E6 + E9: buffer line in array, track column, when near 80 scan back for last blank, output and continue."),
    ("Rewrite the temperature conversion program using a function for the conversion formula.",
     "Walk E7 + E3: float celsius(int fahr) { return (5.0/9.0)*(fahr-32); } called inside a for loop."),
], start=1):
    E.append(KeepTogether([problem_box([
        Paragraph(f"<b>Problem {i}:</b> {q}", styles['CalloutText']),
    ], styles)]))
    E.append(Spacer(1, 0.05*inch))

E.append(Spacer(1, 0.1*inch))
E.append(checkpoint_box([
    Paragraph("CHECKPOINT D", styles['CheckpointText']),
    Paragraph("Complete at least 3 of the 5 problems above. Report your solutions in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── ANSWER KEY ──
E.append(Paragraph("Answer key", styles['SectionHead']))
for i, (q, a) in enumerate([
    ("Reverse lines", "Walk E5 + E9: read into char array with getline, then loop from len-1 down to 0 calling putchar."),
    ("Remove trailing blanks", "Walk E9 + E6: find the \\0, scan backwards past spaces/tabs, place new \\0."),
    ("Tab expansion", "Walk E5 + E3: track column count, on tab output spaces until column % 8 == 0."),
    ("Line folding", "Walk E5 + E6 + E9: buffer line in array, track column, when near 80 scan back for last blank."),
    ("Conversion function", "Walk E7 + E3: define float celsius(int fahr), call inside a for loop with printf."),
], start=1):
    E.append(Paragraph(f"<b>{i}. {q}:</b> {a}", styles['Body']))
    E.append(Spacer(1, 0.04*inch))

build_chapter_pdf("/mnt/user-data/outputs/Chapter_1_Tutorial_Super.pdf", E)
print("Chapter 1 PDF generated successfully.")
