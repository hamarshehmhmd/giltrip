/* Crashes the interpreter on import: giltrip must isolate it and keep going. */
#include <Python.h>
#include <stdlib.h>
PyMODINIT_FUNC PyInit_boom(void) { abort(); return NULL; }
