/* Multi-phase init without a Py_mod_gil slot: re-enables the GIL. */
#include <Python.h>
static PyModuleDef_Slot slots[] = {{0, NULL}};
static struct PyModuleDef def = {PyModuleDef_HEAD_INIT, "gilneed", NULL, 0, NULL, slots};
PyMODINIT_FUNC PyInit_gilneed(void) { return PyModuleDef_Init(&def); }
