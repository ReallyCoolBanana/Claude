#!/usr/bin/env python3
"""Generate Chapter 8: The UNIX System Interface Super PDF."""
import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
E = []

E.append(Paragraph("Chapter 8: The UNIX System Interface", styles['ChapterTitle']))
E.append(Paragraph("Relational graph edition. Pull-shaped. Top-down.", styles['Subtitle']))

E.append(Paragraph("How to use this document", styles['SectionHead']))
E.append(Paragraph("Red = checkpoint. Green = practice. Blue = target. Walk edges in order.", styles['Body']))

E.append(Paragraph("Big picture", styles['SectionHead']))
E.append(Paragraph(
    "Chapter 7 taught you FILE* and printf/scanf. This chapter shows you what is underneath. "
    "File descriptors are the kernel's handles to open files. read() and write() are the fundamental "
    "system calls. Everything in Chapter 7 -- fopen, fprintf, fgets -- is a wrapper built on top of this layer. "
    "Understanding this layer is the difference between using C and understanding C.",
    styles['Body']))

E.append(Paragraph("Target problem", styles['SectionHead']))
E.append(target_box([
    Paragraph("<b>Write a simplified <font name='Courier'>cat</font> command using low-level "
              "read/write system calls. It should: open named files from command-line arguments "
              "(or read stdin if no args), copy contents to stdout, and print errors to stderr.</b>",
              styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.15*inch))

E.append(Paragraph("The Graph", styles['SectionHead']))
E.append(Paragraph("Two-layer architecture (memorize this relationship)", styles['SubHead']))
E.append(concept_box([
    Paragraph("<b>Layer 2 (Chapter 7):</b> FILE* &rarr; fopen/fclose &rarr; fprintf/fscanf &rarr; fgets/fputs",
              styles['CalloutText']),
    Paragraph("<b>Layer 1 (This chapter):</b> int fd &rarr; open/close &rarr; read/write &rarr; lseek",
              styles['CalloutText']),
    Paragraph("Layer 2 is built ON TOP of Layer 1. FILE* wraps a file descriptor + a user-space buffer.",
              styles['CalloutText']),
], styles))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Node inventory", styles['SubHead']))
E.append(make_table(
    ["Node", "Type", "Purpose"],
    [
        ["file descriptor", "int", "Kernel handle to an open file. Small non-negative integer"],
        ["0, 1, 2", "int", "stdin, stdout, stderr -- always open at program start"],
        ["open(path, flags, mode)", "syscall", "Opens file, returns fd. Flags: O_RDONLY, O_WRONLY, O_RDWR, O_CREAT"],
        ["creat(path, mode)", "syscall", "Create/truncate file. Equivalent to open with O_WRONLY|O_CREAT|O_TRUNC"],
        ["close(fd)", "syscall", "Release file descriptor back to kernel"],
        ["read(fd, buf, n)", "syscall", "Read up to n bytes into buf. Returns bytes read, 0 at EOF, -1 on error"],
        ["write(fd, buf, n)", "syscall", "Write n bytes from buf. Returns bytes written, -1 on error"],
        ["lseek(fd, offset, origin)", "syscall", "Move read/write position. origin: SEEK_SET/SEEK_CUR/SEEK_END"],
        ["unlink(path)", "syscall", "Remove directory entry (delete file when last link removed)"],
        ["BUFSIZ", "constant", "Optimal buffer size for I/O (defined in stdio.h, typically 4096-8192)"],
    ],
    col_widths=[2.2*inch, 0.8*inch, 3.5*inch]
))
E.append(Spacer(1, 0.1*inch))

E.append(Paragraph("Edge inventory", styles['SubHead']))
E.append(make_table(
    ["Edge", "From", "To", "Rule"],
    [
        ["E1", "open()", "fd", "Returns new fd on success, -1 on failure"],
        ["E2", "fd", "read()/write()", "fd selects which open file to operate on"],
        ["E3", "fd", "close()", "Releases the fd for reuse"],
        ["E4", "fd", "lseek()", "Repositions the file offset for random access"],
        ["E5", "read() return", "loop control", "Returns 0 at EOF -- use as loop termination"],
        ["E6", "error", "stderr (fd 2)", "Always write errors to fd 2, never fd 1"],
        ["E7", "FILE*", "fd (fileno)", "FILE struct contains fd + buffer + position"],
    ],
    col_widths=[0.5*inch, 1.3*inch, 1.3*inch, 3.4*inch]
))
E.append(PageBreak())

# ── ZOOM IN ──
E.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

E.append(Paragraph("Edge 1: File descriptors -- the kernel's handles", styles['SubHead']))
E.append(Paragraph(
    "A file descriptor is a small non-negative integer. The kernel maintains a table of open files "
    "per process. fd 0/1/2 are pre-opened as stdin/stdout/stderr. When you call open(), the kernel "
    "picks the lowest available fd number and returns it. When you call close(), that slot becomes "
    "available again.", styles['Body']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 8.1:</b> If a program opens 3 files in sequence (and closes none), "
              "what fd numbers will they get?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> 3, 4, 5. (0, 1, 2 are taken by stdin/stdout/stderr.)",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 2: read() and write() -- the fundamental operations", styles['SubHead']))
E.append(Paragraph(
    "Both take: fd, pointer to buffer, byte count. Both return: actual bytes transferred. "
    "read() returns 0 at EOF, -1 on error. write() returns -1 on error. "
    "Neither guarantees transferring all requested bytes in one call -- always check the return value.",
    styles['Body']))
E.append(Paragraph('#include &lt;unistd.h&gt;', styles['Code']))
E.append(Paragraph('char buf[BUFSIZ];', styles['Code']))
E.append(Paragraph('int n;', styles['Code']))
E.append(Paragraph('while ((n = read(0, buf, BUFSIZ)) > 0)', styles['Code']))
E.append(Paragraph('    write(1, buf, n);', styles['Code']))
E.append(Paragraph(
    "This is a complete stdin-to-stdout copier. Five lines. No FILE*, no printf. "
    "This is the lowest level of I/O in a C program.", styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 8.2:</b> Why do we pass <font name='Courier'>n</font> (actual bytes read) to write, "
              "not BUFSIZ?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> The last read before EOF may return fewer than BUFSIZ bytes. "
              "Writing BUFSIZ would output garbage past the actual data.", styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 3: open() and close()", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>open(name, flags, perms)</font> -- flags control access mode. "
    "Common flags: O_RDONLY (read), O_WRONLY (write), O_RDWR (both), O_CREAT (create if missing), "
    "O_TRUNC (truncate existing). Perms (e.g., 0644) set file permissions when creating.",
    styles['Body']))
E.append(Paragraph('#include &lt;fcntl.h&gt;', styles['Code']))
E.append(Paragraph('int fd = open("data.txt", O_RDONLY);', styles['Code']))
E.append(Paragraph('if (fd == -1) {', styles['Code']))
E.append(Paragraph('    write(2, "cannot open\\n", 12);  /* error to stderr */', styles['Code']))
E.append(Paragraph('    return 1;', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph('/* ... use fd ... */', styles['Code']))
E.append(Paragraph('close(fd);', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 8.3:</b> Write code to create a new file <font name='Courier'>out.txt</font> "
              "for writing with permissions 0644.", styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>int fd = open(\"out.txt\", O_WRONLY|O_CREAT|O_TRUNC, 0644);</font> "
              "or <font name='Courier'>int fd = creat(\"out.txt\", 0644);</font>", styles['CalloutText']),
], styles)]))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Without looking: what are the three pre-opened file descriptors? "
              "What does read() return at EOF? What is the relationship between FILE* and fd?",
              styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Edge 4: lseek() -- random access", styles['SubHead']))
E.append(Paragraph(
    "<font name='Courier'>lseek(fd, offset, origin)</font> moves the read/write position. "
    "origin: SEEK_SET (from start), SEEK_CUR (from current), SEEK_END (from end). "
    "Returns the new position, or -1 on error. Enables random-access file I/O.",
    styles['Body']))
E.append(Paragraph('lseek(fd, 0L, SEEK_SET);   /* rewind to start */', styles['Code']))
E.append(Paragraph('lseek(fd, 100L, SEEK_SET); /* jump to byte 100 */', styles['Code']))
E.append(Paragraph('lseek(fd, -10L, SEEK_END); /* 10 bytes before end */', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 8.4:</b> How would you determine the size of an open file using only lseek?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> <font name='Courier'>long size = lseek(fd, 0L, SEEK_END);</font> "
              "then <font name='Courier'>lseek(fd, 0L, SEEK_SET);</font> to rewind.",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 5: How FILE* wraps fd (the key insight)", styles['SubHead']))
E.append(Paragraph(
    "A FILE struct typically contains: the fd, a pointer to a user-space buffer, "
    "the buffer size, current position in the buffer, flags (read/write/error/EOF). "
    "fopen() calls open() to get a fd, allocates a buffer, returns FILE*. "
    "fgetc() checks the buffer first -- only calls read() when the buffer is empty. "
    "This reduces system call overhead dramatically (one read() per BUFSIZ bytes instead of per byte).",
    styles['Body']))
E.append(Paragraph('/* Simplified FILE structure */', styles['Code']))
E.append(Paragraph('typedef struct {', styles['Code']))
E.append(Paragraph('    int  fd;       /* file descriptor */', styles['Code']))
E.append(Paragraph('    int  cnt;      /* characters left in buffer */', styles['Code']))
E.append(Paragraph('    char *ptr;     /* next character position */', styles['Code']))
E.append(Paragraph('    char *base;    /* buffer start */', styles['Code']))
E.append(Paragraph('    int  flag;     /* mode flags */', styles['Code']))
E.append(Paragraph('} FILE;', styles['Code']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 8.5:</b> Why is buffered I/O (FILE*) faster than calling read() one byte at a time?",
              styles['CalloutText']),
    Paragraph("<b>Answer:</b> Each system call has overhead (context switch to kernel and back). "
              "Buffering amortizes one system call across BUFSIZ bytes instead of one per byte.",
              styles['CalloutText']),
], styles)]))

E.append(Paragraph("Edge 6: malloc/free -- the storage allocator", styles['SubHead']))
E.append(Paragraph(
    "malloc() requests memory from the OS (via sbrk or mmap), manages a free list of available blocks, "
    "and returns a pointer to a block of the requested size. free() returns a block to the free list. "
    "The free list is a linked list of unused memory blocks, each containing a header with the block size "
    "and a pointer to the next free block.", styles['Body']))
E.append(Paragraph(
    "<b>Key rules:</b> Always check malloc's return (NULL = out of memory). "
    "Never use memory after free(). Never free() the same pointer twice. "
    "Never write past the allocated size.", styles['BoldBody']))
E.append(KeepTogether([problem_box([
    Paragraph("<b>Problem 8.6:</b> What happens if you call <font name='Courier'>free(p)</font> "
              "and then read <font name='Courier'>*p</font>?", styles['CalloutText']),
    Paragraph("<b>Answer:</b> Undefined behavior. The memory may have been reused or unmapped. "
              "Anything can happen -- crash, garbage data, or seemingly working (the worst case).",
              styles['CalloutText']),
], styles)]))
E.append(PageBreak())

# ── SOLVE TARGET ──
E.append(Paragraph("Solving the target problem", styles['SectionHead']))
E.append(Paragraph("Walk: E1 (open files from argv) + E2 (read/write loop) + E6 (errors to stderr) + E3 (close)", styles['Body']))
E.append(Paragraph('#include &lt;stdio.h&gt;', styles['Code']))
E.append(Paragraph('#include &lt;fcntl.h&gt;', styles['Code']))
E.append(Paragraph('#include &lt;unistd.h&gt;', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('void filecopy(int fd_in, int fd_out) {', styles['Code']))
E.append(Paragraph('    char buf[BUFSIZ];', styles['Code']))
E.append(Paragraph('    int n;', styles['Code']))
E.append(Paragraph('    while ((n = read(fd_in, buf, BUFSIZ)) > 0)', styles['Code']))
E.append(Paragraph('        write(fd_out, buf, n);', styles['Code']))
E.append(Paragraph('}', styles['Code']))
E.append(Paragraph('', styles['Code']))
E.append(Paragraph('int main(int argc, char *argv[]) {', styles['Code']))
E.append(Paragraph('    if (argc == 1) {', styles['Code']))
E.append(Paragraph('        filecopy(0, 1);  /* stdin to stdout */', styles['Code']))
E.append(Paragraph('    } else {', styles['Code']))
E.append(Paragraph('        for (int i = 1; i &lt; argc; i++) {', styles['Code']))
E.append(Paragraph('            int fd = open(argv[i], O_RDONLY);', styles['Code']))
E.append(Paragraph('            if (fd == -1) {', styles['Code']))
E.append(Paragraph('                char msg[] = "cannot open: ";', styles['Code']))
E.append(Paragraph('                write(2, msg, sizeof(msg)-1);', styles['Code']))
E.append(Paragraph('                write(2, argv[i], strlen(argv[i]));', styles['Code']))
E.append(Paragraph('                write(2, "\\n", 1);', styles['Code']))
E.append(Paragraph('                continue;', styles['Code']))
E.append(Paragraph('            }', styles['Code']))
E.append(Paragraph('            filecopy(fd, 1);', styles['Code']))
E.append(Paragraph('            close(fd);', styles['Code']))
E.append(Paragraph('        }', styles['Code']))
E.append(Paragraph('    }', styles['Code']))
E.append(Paragraph('    return 0;', styles['Code']))
E.append(Paragraph('}', styles['Code']))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("Modify the cat program to also print each filename as a header before its contents "
              "(write the header to stdout using write()). Report your changes.", styles['CalloutText']),
], styles))
E.append(PageBreak())

# ── FINAL EXAM ──
E.append(Paragraph("Final exam", styles['SectionHead']))
for i, q in enumerate([
    "Write a function that copies a file using low-level I/O (given source and dest filenames).",
    "Implement a simplified <font name='Courier'>fgets()</font> using only read() and a static buffer.",
    "Write a program that prints the byte offset of every newline in a file using lseek() to report position.",
    "Explain why closing fd 1 and then opening a file gives that file fd 1 (and thus captures stdout).",
    "Write a simplified malloc that allocates from a static char array (arena allocator).",
], start=1):
    E.append(KeepTogether([problem_box([
        Paragraph(f"<b>Problem {i}:</b> {q}", styles['CalloutText']),
    ], styles)]))
    E.append(Spacer(1, 0.05*inch))

E.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("Solve at least 3 problems. Report in chat.", styles['CalloutText']),
], styles))
E.append(PageBreak())

E.append(Paragraph("Answer key", styles['SectionHead']))
for i, a in enumerate([
    "Walk E1+E2+E3: open source O_RDONLY, open dest O_WRONLY|O_CREAT|O_TRUNC, read/write loop, close both.",
    "Walk E2+E5: static buffer + position. On call, copy from buffer to user string until newline or buffer empty. Refill buffer with read() when empty.",
    "Walk E2+E4: read one byte at a time (or buffer and track offset). When byte is '\\n', print current file offset.",
    "Walk E1: open() returns the lowest available fd. After close(1), fd 1 is free. Next open() gets fd 1. Any write(1,...) now goes to that file.",
    "Static char arena[SIZE]; int pos=0; malloc returns &arena[pos] and advances pos by requested size + alignment. free() is a no-op (or tracks with a free list).",
], start=1):
    E.append(Paragraph(f"<b>{i}.</b> {a}", styles['Body']))
    E.append(Spacer(1, 0.04*inch))

build_chapter_pdf("/mnt/user-data/outputs/Chapter_8_UNIX_Interface_Super.pdf", E)
print("Chapter 8 PDF generated successfully.")
