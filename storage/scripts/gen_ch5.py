#!/usr/bin/env python3
"""Generate Chapter 5: Pointers and Arrays Super PDF."""
import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
E = []

E.append(Paragraph("Chapter 5: Pointers and Arrays", styles['ChapterTitle']))
E.append(Paragraph("Relational graph edition. Pull-shaped. Top-down.", styles['Subtitle']))

E.append(Paragraph("How to use this document", styles['SectionHead']))
E.append(Paragraph("This is the hardest chapter in K&amp;R. Every section builds on the last. "
    "Do not skip problems. Red = checkpoint. Green = practice. Blue = target.", styles['Body']))

E.append(Paragraph("Big picture", styles['SectionHead']))
E.append(Paragraph(
    "A pointer is a variable that holds the address of another variable. "
    "Dereferencing a pointer follows that address to reach the value. "
    "Array names decay to pointers. Pointer arithmetic moves through memory in strides of sizeof(type). "
    "This chapter is the core of C -- everything else (strings, structs, dynamic memory, argv) builds on it.",
    styles['Body']))

E.append(Paragraph("Target problem", styles['SectionHead']))
E.append(target_box([
    Paragraph("<b>Write a program that sorts command-line arguments (argv) alphabetically "
              "using a sort function that takes a pointer-to-function comparator, "
              "demonstrating: argv as pointer array, pointer arithmetic, and function pointers.</b>",
              styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.15*inch))

E.append(Paragraph("The Graph", styles['SectionHead']))
E.append(Paragraph("Node inventory", styles['SubHead']))
E.append(make_table(
    ["Node", "Syntax", "What it holds"],
    [
        ["pointer variable", "int *p;", "An address (memory location of another variable)"],
        ["address-of", "&amp;x", "Produces the address where x is stored"],
        ["dereference", "*p", "Follows the address in p to get the value there"],
        ["array name", "int a[10]; then a", "Decays to pointer to first element (&amp;a[0])"],
        ["NULL", "(void *)0", "Invalid address -- means 'points to nothing'"],
        ["void *", "void *p;", "Generic pointer -- can hold any address, must cast to use"],
        ["pointer arithmetic", "p + n", "Moves n * sizeof(*p) bytes forward"],
        ["argv / argc", "char *argv[]", "Array of string pointers from command line"],
        ["function pointer", "int (*fp)(int, int)", "Holds address of a function; call via (*fp)(args)"],
        ["sizeof", "sizeof(type)", "Returns size in bytes -- the stride for pointer arithmetic"],
    ],
    col_widths=[1.4*inch, 1.8*inch, 3.3*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Edge inventory", styles['SubHead']))
E.append(make_table(
    ["Edge", "Rule"],
    [
        ["E1: &amp; produces pointer", "&amp;x gives you a pointer to x. Type of &amp;x is 'pointer to type-of-x'"],
        ["E2: * follows pointer", "*p gives you the value at the address stored in p"],
        ["E3: array decays", "Array name used in expression becomes pointer to element 0. a[i] == *(a+i)"],
        ["E4: pointer + int", "p+n moves forward n elements (n * sizeof(*p) bytes). p-n moves back"],
        ["E5: pointer - pointer", "p-q gives the number of elements between two pointers (same array only)"],
        ["E6: function pointer", "Function name without () is a pointer to that function. Call via fp(args) or (*fp)(args)"],
        ["E7: argv structure", "argv[0] = program name, argv[1..argc-1] = arguments, argv[argc] = NULL"],
    ],
    col_widths=[1.8*inch, 4.7*inch]
))
E.append(PageBreak())

E.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

E.append(Paragraph("Edge 1: Pointers and addresses", styles['SubHead']))
E.append(Paragraph(
    "Every variable lives at a specific memory address. "
    "<font name='Courier'>&amp;x</font> gives you that address. "
    "A pointer variable stores an address. "
    "The declaration <font name='Courier'>int *p;</font> means 'p is a variable that holds the address of an int.'",
    styles['Body']))

E.append(Paragraph("Memory layout example:", styles['BoldBody']))
E.append(make_table(
    ["Variable", "Address", "Value"],
    [
        ["int x", "0x1000", "42"],
        ["int *p", "0x1008", "0x1000 (address of x)"],
    ],
    col_widths=[1.5*inch, 1.5*inch, 3.5*inch]
))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph('int x = 42;', styles['Code']))
E.append(Paragraph('int *p = &amp;x;    /* p holds the address of x */', styles['Code']))
E.append(Paragraph('printf("%d\\n", *p);  /* follows p to get 42 */', styles['Code']))
E.append(Paragraph('*p = 99;             /* changes x through p */', styles['Code']))
E.append(Paragraph('printf("%d\\n", x);   /* prints 99 */', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.1:</b> If <font name='Courier'>int a=5, b=10, *p=&amp;a, *q=&amp;b;</font>, "
              "what does <font name='Courier'>*p + *q</font> evaluate to?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> 15. *p is 5 (value at a), *q is 10 (value at b). Sum is 15.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 2: Pointers and function arguments (the swap problem)", styles['SubHead']))
E.append(Paragraph(
    "C is call-by-value. To let a function modify the caller's variable, pass a pointer to it. "
    "The function receives a copy of the pointer (the address), and uses * to modify the original.",
    styles['Body']))
E.append(Paragraph('void swap(int *px, int *py) {', styles['Code']))
E.append(Paragraph('    int temp = *px;', styles['Code']))
E.append(Paragraph('    *px = *py;', styles['Code']))
E.append(Paragraph('    *py = temp;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph('/* Call: swap(&amp;a, &amp;b); -- pass addresses */', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.2:</b> Write a function that takes two int pointers and sets both values to their sum. "
              "<font name='Courier'>void add_to_both(int *a, int *b)</font>", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>int sum = *a + *b; *a = sum; *b = sum;</font>", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 3: Arrays decay to pointers", styles['SubHead']))
E.append(Paragraph(
    "When an array name appears in an expression, it becomes a pointer to element 0. "
    "<font name='Courier'>a[i]</font> is identical to <font name='Courier'>*(a + i)</font>. "
    "This is not a metaphor -- the compiler generates the same code for both. "
    "Exception: sizeof(a) gives the total array size, not pointer size.",
    styles['Body']))
E.append(make_table(
    ["Expression", "Equivalent", "Type"],
    [
        ["a", "&amp;a[0]", "int * (pointer to first element)"],
        ["a[3]", "*(a + 3)", "int (value of 4th element)"],
        ["&amp;a[3]", "a + 3", "int * (pointer to 4th element)"],
    ],
    col_widths=[1.5*inch, 1.5*inch, 3.5*inch]
))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.3:</b> If <font name='Courier'>int a[] = {10, 20, 30, 40};</font> and "
              "<font name='Courier'>int *p = a;</font>, what is <font name='Courier'>*(p+2)</font>?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> 30. p points to a[0], p+2 points to a[2], *(p+2) is the value 30.", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Without looking: what is the difference between &amp;x and *p? "
              "Why must swap() take pointers instead of values? "
              "What does a[i] actually mean in terms of pointer arithmetic?", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Edge 4: Pointer arithmetic", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>p + n</font> moves forward n elements, not n bytes. "
    "If p is <font name='Courier'>int *</font> and sizeof(int) is 4, then p+1 advances 4 bytes. "
    "The compiler multiplies by sizeof(*p) automatically. "
    "Subtracting two pointers gives element count between them.",
    styles['Body']))
E.append(make_table(
    ["If p = 0x1000 (int *)", "Expression", "Address"],
    [
        ["", "p", "0x1000"],
        ["", "p + 1", "0x1004 (moved 4 bytes)"],
        ["", "p + 3", "0x100C (moved 12 bytes)"],
    ],
    col_widths=[2.0*inch, 1.5*inch, 3.0*inch]
))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.4:</b> If <font name='Courier'>double *dp</font> is at address 0x2000, "
              "what is the address of dp+5? (sizeof(double) = 8)", styles['CalloutText']),
    Paragraph("<b>Answer:</b> 0x2000 + 5*8 = 0x2028.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 5: Character pointers and strings", styles['SubHead']))
E.append(Paragraph(
    "A string literal <font name='Courier'>\"hello\"</font> is stored as a char array with \\0 terminator. "
    "A <font name='Courier'>char *</font> can point to the first character. "
    "String functions like strcpy/strcmp walk the pointer forward until they hit \\0.",
    styles['Body']))
E.append(Paragraph('/* strcpy using pointers */', styles['Code']))
E.append(Paragraph('void my_strcpy(char *dest, char *src) {', styles['Code']))
E.append(Paragraph("    while ((*dest++ = *src++) != '\\0')", styles['Code']))
E.append(Paragraph('        ;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph(
    "<b>Parsing *dest++ = *src++:</b> (1) copy *src to *dest, (2) increment both pointers, "
    "(3) test if copied char was \\0. Three operations in one expression.", styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.5:</b> Write <font name='Courier'>int my_strcmp(char *s, char *t)</font> "
              "that returns &lt;0, 0, or &gt;0.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>for (; *s == *t; s++, t++) if (*s == '\\0') return 0; "
              "return *s - *t;</font>", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 6: Pointer arrays and argv", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>char *argv[]</font> is an array of pointers-to-char. "
    "Each element points to a null-terminated string. argv[0] is the program name. "
    "argv[argc] is NULL. This is how the OS passes command-line arguments to your program.",
    styles['Body']))
E.append(make_table(
    ["Index", "argv[i]", "Points to"],
    [
        ["0", "argv[0]", "\"./myprogram\" (program name)"],
        ["1", "argv[1]", "\"hello\" (first argument)"],
        ["2", "argv[2]", "\"world\" (second argument)"],
        ["3", "argv[3]", "NULL (sentinel)"],
    ],
    col_widths=[0.8*inch, 1.5*inch, 4.2*inch]
))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.6:</b> Write a program that prints command-line arguments one per line, using a pointer "
              "instead of an index.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>while (*++argv) printf(\"%s\\n\", *argv);</font> "
              "(skip argv[0], print until NULL)", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("Write <font name='Courier'>int my_strlen(char *s)</font> using pointer arithmetic only "
              "(no indexing, no counter variable). Hint: remember where you started.", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Edge 7: Function pointers", styles['SubHead']))
E.append(Paragraph(
    "A function name without () is a pointer to that function. "
    "You can store it in a variable and call it later. "
    "This enables polymorphism: pass different comparison functions to the same sort routine.",
    styles['Body']))
E.append(Paragraph('/* Function pointer declaration */', styles['Code']))
E.append(Paragraph('int (*compare)(const char *, const char *);', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('/* Assign and call */', styles['Code']))
E.append(Paragraph('compare = strcmp;', styles['Code']))
E.append(Paragraph('int result = compare("abc", "def");  /* calls strcmp */', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('/* Reading the declaration: */', styles['Code']))
E.append(Paragraph('/* (*compare) = "compare is a pointer" */', styles['Code']))
E.append(Paragraph('/* (*compare)(...) = "to a function taking ..." */', styles['Code']))
E.append(Paragraph('/* int (*compare)(...) = "returning int" */', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 5.7:</b> Declare a function pointer <font name='Courier'>fp</font> "
              "that points to a function taking two ints and returning void.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>void (*fp)(int, int);</font>", styles['CalloutText']),
], styles)]))
E.append(PageBreak())

# ── SOLVE TARGET ──
E.append(Paragraph("Solving the target problem", styles['SectionHead']))
E.append(Paragraph("Walk: E6 (argv as pointer array) + E5 (strings) + E7 (function pointer for comparison) + E4 (pointer arithmetic in sort)", styles['Body']))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('#include &lt;string.h&gt;', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('void sort_strings(char *arr[], int n,', styles['Code']))
E.append(Paragraph('                  int (*cmp)(const char *, const char *)) {', styles['Code']))
E.append(Paragraph('    for (int i = 0; i &lt; n-1; i++)', styles['Code']))
E.append(Paragraph('        for (int j = i+1; j &lt; n; j++)', styles['Code']))
E.append(Paragraph('            if (cmp(arr[i], arr[j]) > 0) {', styles['Code']))
E.append(Paragraph('                char *tmp = arr[i];', styles['Code']))
E.append(Paragraph('                arr[i] = arr[j];', styles['Code']))
E.append(Paragraph('                arr[j] = tmp;', styles['Code']))
E.append(Paragraph('            }', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int main(int argc, char *argv[]) {', styles['Code']))
E.append(Paragraph('    sort_strings(argv + 1, argc - 1, strcmp);', styles['Code']))
E.append(Paragraph('    for (int i = 1; i &lt; argc; i++)', styles['Code']))
E.append(Paragraph('        printf("%s\\n", argv[i]);', styles['Code']))
E.append(Paragraph('    return 0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Spacer(1, 0.05*inch))
E.append(Paragraph(
    "<b>Edges walked:</b> E6 (argv+1 skips program name), E7 (strcmp passed as function pointer), "
    "E5 (string comparison), E3 (arr[i] is pointer dereference).", styles['BoldBody']))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("Modify the program to accept a -r flag for reverse sort. "
              "When -r is present, pass a reverse_strcmp function instead of strcmp.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── FINAL EXAM ──
E.append(Paragraph("Final exam", styles['SectionHead']))
for i, q in enumerate([
    "Write <font name='Courier'>void strcat(char *s, char *t)</font> using pointer arithmetic (no indexing).",
    "Write a function that returns a pointer to the last occurrence of char c in string s, or NULL if not found.",
    "Explain the difference between <font name='Courier'>char s[]</font> and <font name='Courier'>char *s</font> when initialized with a string literal.",
    "Write <font name='Courier'>int day_of_year(int year, int month, int day)</font> using a 2D array of month lengths.",
    "Decode this declaration: <font name='Courier'>char (*(*x())[])();</font>",
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
    "Walk E4+E5: advance s to end (while(*s) s++), then copy t (while((*s++ = *t++))).",
    "Walk E4+E5: scan from start, remember last position where *s==c. Return that pointer or NULL.",
    "char s[] = \"hello\" creates a modifiable 6-byte array. char *s = \"hello\" creates a pointer to a read-only string literal. Modifying via s[0]='H' is legal for the array, undefined behavior for the pointer.",
    "Walk E3: static int daytab[2][13] with leap/non-leap month lengths. Sum months 1 through month-1, add day.",
    "x is a function returning a pointer to an array of pointers to functions returning char. (Use the spiral/clockwise rule: start at x, go right for (), left for *, right for [], etc.)",
], start=1):
    E.append(Paragraph(f"<b>{i}.</b> {a}", styles['Body']))
    E.append(Spacer(1, 0.04*inch))

build_chapter_pdf("/mnt/user-data/outputs/Chapter_5_Pointers_Arrays_Super.pdf", E)
print("Chapter 5 PDF generated successfully.")
