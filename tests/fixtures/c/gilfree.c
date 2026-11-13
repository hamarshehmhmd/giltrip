/* Declares that it runs without the GIL: must NOT be reported. */
#include <Python.h>
static PyModuleDef_Slot slots[] = {
#ifdef Py_GIL_DISABLED
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL}};
static struct PyModuleDef def = {PyModuleDef_HEAD_INIT, "gilfree", NULL, 0, NULL, slots};
PyMODINIT_FUNC PyInit_gilfree(void) { return PyModuleDef_Init(&def); }
