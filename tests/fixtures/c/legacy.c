/* Old single-phase init: always treated as needing the GIL. */
#include <Python.h>
static struct PyModuleDef def = {PyModuleDef_HEAD_INIT, "legacy", NULL, -1, NULL};
PyMODINIT_FUNC PyInit_legacy(void) { return PyModule_Create(&def); }
