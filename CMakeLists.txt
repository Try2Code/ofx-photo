cmake_minimum_required(VERSION 3.16)
project(ofx-photo VERSION 0.5.2 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Default to a per-user prefix rather than /usr/local, since this is a desktop
# tool and installing it needs no root.
if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
  set(CMAKE_INSTALL_PREFIX "$ENV{HOME}/.local" CACHE PATH "install prefix" FORCE)
endif()

include(GNUInstallDirs)

add_executable(spektra-render
  src/main.cpp
  src/host.cpp
  src/suites.cpp
)
target_include_directories(spektra-render PRIVATE ofx-include)
target_link_libraries(spektra-render PRIVATE dl)

# ---------------------------------------------------------------- installation

install(TARGETS spektra-render RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

# The two front ends sit beside the host binary; spektra locates
# spektra-render next to itself, and spektra-gui imports spektra the same way.
install(PROGRAMS spektra spektra-gui DESTINATION ${CMAKE_INSTALL_BINDIR})

# Desktop entry, pointed at wherever this prefix put the launcher.
set(OFX_PHOTO_EXEC "${CMAKE_INSTALL_FULL_BINDIR}/spektra-gui")
set(OFX_PHOTO_ICON "ofx-photo")
configure_file(desktop/ofx-photo.desktop.in
               "${CMAKE_CURRENT_BINARY_DIR}/ofx-photo.desktop" @ONLY)

# Where the menu entry and icons go.  A desktop session reads its environment
# at login and never sees a shell rc, so a prefix like ~/local will not show up
# in the menu however PATH is set.  XDG_DATA_HOME is always searched, so by
# default the integration goes there while the programs stay in the prefix.
option(DESKTOP_TO_PREFIX
       "Install the menu entry and icons under the prefix instead of XDG_DATA_HOME" OFF)

# A prefix under a temporary directory is a trial install, not the one the
# person wants in their menu.  Sending its entry to XDG_DATA_HOME would
# overwrite the real one with an Exec line that stops resolving as soon as the
# directory is cleaned up, and an entry whose Exec is gone is dropped by glib
# entirely -- no menu item, and no icon on the window either.  Keep such an
# install to itself.
set(_desktop_to_prefix ${DESKTOP_TO_PREFIX})
if(NOT _desktop_to_prefix)
  set(_tmproots "/tmp" "/var/tmp")
  if(DEFINED ENV{TMPDIR})
    file(TO_CMAKE_PATH "$ENV{TMPDIR}" _tmpdir)
    string(REGEX REPLACE "/+$" "" _tmpdir "${_tmpdir}")
    list(APPEND _tmproots "${_tmpdir}")
  endif()
  get_filename_component(_prefix "${CMAKE_INSTALL_PREFIX}" ABSOLUTE)
  foreach(_root IN LISTS _tmproots)
    # Plain prefix test: the paths come from the environment and must not be
    # read as regular expressions.
    string(LENGTH "${_root}/" _n)
    string(SUBSTRING "${_prefix}/" 0 ${_n} _head)
    if(_head STREQUAL "${_root}/")
      set(_desktop_to_prefix ON)
      message(STATUS
              "Prefix is below ${_root}; keeping the menu entry inside it "
              "rather than overwriting the one in XDG_DATA_HOME")
      break()
    endif()
  endforeach()
endif()

# DESTINATION stays relative under the prefix so DESTDIR still works, but the
# cache updaters below need somewhere they can actually chdir to.
if(_desktop_to_prefix)
  set(OFX_PHOTO_DESKTOP_DIR "${CMAKE_INSTALL_DATADIR}")
  set(OFX_PHOTO_DESKTOP_FULL "${CMAKE_INSTALL_FULL_DATADIR}")
else()
  if(DEFINED ENV{XDG_DATA_HOME})
    set(OFX_PHOTO_DESKTOP_DIR "$ENV{XDG_DATA_HOME}")
  else()
    set(OFX_PHOTO_DESKTOP_DIR "$ENV{HOME}/.local/share")
  endif()
  set(OFX_PHOTO_DESKTOP_FULL "${OFX_PHOTO_DESKTOP_DIR}")
endif()

install(FILES "${CMAKE_CURRENT_BINARY_DIR}/ofx-photo.desktop"
        DESTINATION "${OFX_PHOTO_DESKTOP_DIR}/applications")
install(FILES desktop/ofx-photo.svg
        DESTINATION "${OFX_PHOTO_DESKTOP_DIR}/icons/hicolor/scalable/apps")
foreach(size 16 24 32 48 64 128 256)
  install(FILES desktop/icons/${size}/ofx-photo.png
          DESTINATION "${OFX_PHOTO_DESKTOP_DIR}/icons/hicolor/${size}x${size}/apps")
endforeach()

install(CODE "
  execute_process(COMMAND update-desktop-database \"\$ENV{DESTDIR}${OFX_PHOTO_DESKTOP_FULL}/applications\"
                  ERROR_QUIET RESULT_VARIABLE _ignored)
  execute_process(COMMAND gtk-update-icon-cache -qtf \"\$ENV{DESTDIR}${OFX_PHOTO_DESKTOP_FULL}/icons/hicolor\"
                  ERROR_QUIET RESULT_VARIABLE _ignored)
  message(STATUS \"Menu entry and icons: \$ENV{DESTDIR}${OFX_PHOTO_DESKTOP_FULL}\")
  # A desktop entry whose Exec program is missing is not merely broken: glib
  # discards the whole file, so the window manager has nothing to match the
  # window's app id against and falls back to a generic icon.  Installing to a
  # throwaway prefix writes exactly such an entry over the real one, so say so
  # while the person is still looking at the install output.
  if(NOT EXISTS \"\$ENV{DESTDIR}${OFX_PHOTO_EXEC}\")
    message(WARNING
      \"The menu entry points at ${OFX_PHOTO_EXEC}, which does not exist.\\n\"
      \"Desktop environments will ignore the entry entirely and show no icon.\\n\"
      \"Install to a real prefix, or pass -DDESKTOP_TO_PREFIX=ON (or DESTDIR=...)\\n\"
      \"to keep a scratch install out of \\$XDG_DATA_HOME.\")
  endif()
")

# Where the programs landed, and how to reach them.
install(CODE "
  message(STATUS \"\")
  message(STATUS \"Programs: ${CMAKE_INSTALL_FULL_BINDIR}\")
  if(NOT \"${CMAKE_INSTALL_FULL_BINDIR}\" MATCHES \"^(/usr/bin|/usr/local/bin|$ENV{HOME}/.local/bin)$\")
    message(STATUS \"Add it to your PATH to run them by name:\")
    message(STATUS \"    export PATH=\\\"${CMAKE_INSTALL_FULL_BINDIR}:\\$PATH\\\"\")
  endif()
  message(STATUS \"\")
")
