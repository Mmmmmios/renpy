# Copyright 2004-2025 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from __future__ import print_function
import re
import io
import os

import renpy

# A map from shader part name to ShaderPart
shader_part = {}


def register_shader(name, **kwargs):
    """
    :doc: register_shader

    This registers a shader part. This takes `name`, and then
    keyword arguments.

    `name`
        A string giving the name of the shader part. Names starting with an
        underscore or "renpy." are reserved for Ren'Py.

    `variables`
        The variables used by the shader part. These should be listed one per
        line, a storage (uniform, attribute, or varying) followed by a type,
        name, and semicolon. For example::

            variables='''
            uniform sampler2D tex0;
            attribute vec2 a_tex_coord;
            varying vec2 v_tex_coord;
            '''

    `vertex_functions`
        If given, a string containing functions that will be included in the
        vertex shader.

    `fragment_functions`
        If given, a string containing functions that will be included in the
        fragment shader.

    Other keyword arguments should start with ``vertex_`` or ``fragment_``,
    and end with an integer priority. So "fragment_200" or "vertex_300". These
    give text that's placed in the appropriate shader at the given priority,
    with lower priority numbers inserted before higher priority numbers.
    """

    return ShaderPart(name, **kwargs)


class ShaderPart(object):
    """
    Arguments are as for register_shader.

    """

    def __init__(
        self, name, variables="", vertex_functions="", fragment_functions="", private_uniforms=False, **kwargs
    ):
        if not re.match(r"^[\w\.]+$", name):
            raise Exception(
                "The shader name {!r} contains an invalid character. Shader names are limited to ASCII alphanumeric characters, _, and .".format(
                    name
                )
            )

        self.name = name
        shader_part[name] = self

        self.vertex_functions = vertex_functions
        self.fragment_functions = fragment_functions

        # A list of priority, text pairs for each section of the vertex and fragment shaders.
        self.vertex_parts = []
        self.fragment_parts = []

        # Sets of (storage, type, name) tuples, where storage is one of 'uniform', 'attribute', or 'varying',
        self.vertex_variables = set()
        self.fragment_variables = set()

        # A map from variable name to type.
        self.variable_types = {}

        # A sets of variable names used in the vertex and fragments shader.
        vertex_used = set()
        fragment_used = set()

        self.uniforms = []

        for k, v in kwargs.items():
            shader, _, priority = k.partition("_")

            v = self.substitute_name(v)

            if not priority:
                # Trigger error handling.
                shader = None

            try:
                priority = int(priority)
            except Exception:
                shader = None

            if shader == "vertex":
                parts = self.vertex_parts
                used = vertex_used
            elif shader == "fragment":
                parts = self.fragment_parts
                used = fragment_used
            else:
                raise Exception("Keyword arguments to ShaderPart must be of the form {vertex,fragment}_{priority}.")

            parts.append((priority, name, v))

            for m in re.finditer(r"\b\w+\b", v):
                used.add(m.group(0))

        variables = self.substitute_name(variables)

        for l in variables.split("\n"):
            l = l.partition("//")[0]
            l = l.strip()
            if not l:
                continue

            v = renpy.gl2.gl2shader.Variable(self.name, l)

            if v.storage not in {"uniform", "attribute", "varying"}:
                raise Exception(
                    "In shader {}: Unknown shader variable line {!r}. Only the form '{{uniform,attribute,vertex}} {{type}} {{name}} is allowed.".format(
                        self.name, l
                    )
                )

            if v.array:
                self.variable_types[v.name] = v.type + "[]"
            else:
                self.variable_types[v.name] = v.type

            if v.name in vertex_used:
                self.vertex_variables.add(v)

            if v.name in fragment_used:
                self.fragment_variables.add(v)

            if v.storage == "uniform" and not private_uniforms:
                renpy.display.transform.add_uniform(v.name, v.type)

            if v.storage == "uniform":
                self.uniforms.append(v.name)

        self.raw_variables = variables

    def expand_name(self, s):
        """
        Expands names starting with u__, a__, and v__ to include the shader part name.
        """

        name = self.name.replace(".", "_")

        if s.startswith("u__"):
            return "u_" + name + "_" + s[3:]
        elif s.startswith("a__"):
            return "a_" + name + "_" + s[3:]
        elif s.startswith("v__"):
            return "v_" + name + "_" + s[3:]
        elif s.startswith("l__"):
            return "l_" + name + "_" + s[3:]
        else:
            return s

    def expand_match(self, m):
        """
        Expands a match object using expand_name.
        """

        return self.expand_name(m.group(0))

    def expand_operation(self, m):
        """
        Expands an operation match object using expand_name.
        """

        return "u_{}_OP_{}".format(m.group(1), m.group(2))

    def substitute_name(self, s):
        rv = re.sub(r"\b[uavl]__\w+", self.expand_match, s)
        rv = re.sub(r"\bu_(\w+)__(\w+)", self.expand_operation, rv)
        return rv


# A map from a tuple giving the parts that comprise a shader, to the Shader
# object. The same shader might appear multiple times, to optimize performance.
cache = {}


def source(variables, parts, functions, fragment, gles):
    """
    Given lists of variables and parts, converts them into textual source
    code for a shader.

    `fragment`
        Should be set to true to generate the code for a fragment shader.
    """

    rv = []

    if gles:
        rv.append("""\
#version 100
""")

        if fragment:
            rv.append("""\
#ifdef GL_FRAGMENT_PRECISION_HIGH
    precision highp float;
    precision highp int;
#else
    precision mediump float;
    precision mediump int;
#endif
""")

    else:
        rv.append("""\
#version 120
""")

    for v in sorted(variables, key=lambda x: x.name):
        rv.append(v.line + ";\n")

    rv.extend(functions)

    rv.append("\nvoid main() {\n")

    parts.sort()

    for _, _, part in parts:
        rv.append(part)

    rv.append("}\n")

    return "".join(rv)


shader_part_filter_cache = {}


class ShaderCache(object):
    """
    This class caches shaders that were compiled. It's also responsible for
    recording shaders that have been used, persisting them to disk, and then
    loading the shaders back into the cache.
    """

    def __init__(self, filename, gles):
        # The filename that we'll load the list of shaders from, and
        # persist it to.
        self.filename = filename

        # Are we gles?
        self.gles = gles

        # A map from tuples of partnames to the shaders that have been
        # created.
        self.cache = {}

        # A set of tuples of partnames corresponding to shaders that existed
        # in the past, but do not exist now.
        self.missing = set()

        # True if this is dirty, and should be saved to the cache.
        self.dirty = False

    def get(self, partnames):
        """
        Gets a shader, creating it if necessary.

        `partnames`
            A tuple of strings, giving the names of the shader parts to include in
            the cache.
        """

        if renpy.config.shader_part_filter is not None:
            new_partnames = shader_part_filter_cache.get(partnames, None)
            if new_partnames is None:
                new_partnames = renpy.config.shader_part_filter(partnames)
                shader_part_filter_cache[partnames] = new_partnames

            partnames = new_partnames

        rv = self.cache.get(partnames, None)
        if rv is not None:
            return rv

        partnameset = set()
        partnamenotset = set()

        for i in partnames:
            if i.startswith("-"):
                partnamenotset.add(i[1:])
            else:
                partnameset.add(i)

        partnameset -= partnamenotset

        if "renpy.ftl" not in partnameset:
            partnameset.add(renpy.config.default_shader)

        sortedpartnames = tuple(sorted(partnameset))

        rv = self.cache.get(sortedpartnames, None)
        if rv is not None:
            self.cache[partnames] = rv
            return rv

        # If the cache missed entirely, we have to generate the source code for the
        # shaders.

        vertex_variables = set()
        vertex_parts = []
        vertex_functions = []

        fragment_variables = set()
        fragment_parts = []
        fragment_functions = []

        for i in sortedpartnames:
            p = shader_part.get(i, None)

            if p is None:
                raise Exception("{!r} is not a known shader part.".format(i))

            vertex_variables |= p.vertex_variables
            vertex_parts.extend(p.vertex_parts)
            vertex_functions.append(p.vertex_functions)

            fragment_variables |= p.fragment_variables
            fragment_parts.extend(p.fragment_parts)
            fragment_functions.append(p.fragment_functions)

        vertex = source(vertex_variables, vertex_parts, vertex_functions, False, self.gles)
        fragment = source(fragment_variables, fragment_parts, fragment_functions, True, self.gles)

        self.log_shader("vertex", sortedpartnames, vertex)
        self.log_shader("fragment", sortedpartnames, fragment)

        from renpy.gl2.gl2shader import Program

        rv = Program(sortedpartnames, vertex, fragment)
        rv.load()

        self.cache[partnames] = rv
        self.cache[sortedpartnames] = rv

        self.dirty = True

        return rv

    def check(self, partnames):
        """
        Returns true if every part in partnames is a known part, or False
        otherwise.
        """

        for i in partnames:
            if i not in shader_part:
                return False

        return True

    def save(self):
        """
        Saves the list of shaders to the file.
        """

        if not self.dirty:
            return

        if not renpy.config.developer:
            return

        fn = "<unknown>"

        try:
            fn = os.path.join(renpy.config.gamedir, renpy.loader.get_path(self.filename))

            tmp = fn + ".tmp"

            with io.open(tmp, "w", encoding="utf-8") as f:
                shaders = set(self.cache.keys()) | self.missing

                for i in shaders:
                    f.write(" ".join(i) + "\r\n")

            try:
                os.unlink(fn)
            except Exception:
                pass

            os.rename(tmp, fn)

            self.dirty = False

        except Exception:
            renpy.display.log.write("Saving shaders to {!r}:".format(fn))
            renpy.display.log.exception()

    def load(self):
        """
        Loads the list of shaders from the file, and compiles all shaders
        for which the parts exist, and for which compilation can succeed.
        """

        try:
            with renpy.loader.load(self.filename) as f:
                for l in f:
                    l = l.strip().decode("utf-8")
                    partnames = tuple(l.strip().split())

                    if not partnames:
                        continue

                    if not self.check(partnames):
                        self.missing.add(partnames)
                        continue

                    try:
                        self.get(partnames)
                    except Exception:
                        renpy.display.log.write("Precompiling shader {!r}:".format(partnames))
                        renpy.display.log.exception()
                        self.missing.add(partnames)
        except Exception:
            renpy.display.log.write("Could not open {!r}:".format(self.filename))
            return

    def clear(self):
        """
        Clears the shader cache and the shaders inside it.
        """

        self.cache.clear()
        self.missing.clear()

    def log_shader(self, kind, partnames, text):
        """
        Logs the shader text to the log.
        """

        if not renpy.config.log_gl_shaders:
            return

        name = kind + " " + ", ".join(partnames) + " "
        name = name + "-" * max(0, 80 - len(name))

        renpy.display.log.write("%s", name)
        renpy.display.log.write("%s", text)
        renpy.display.log.write("-" * 80)
