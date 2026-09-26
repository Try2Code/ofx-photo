cmake_minimum_required(VERSION 3.16)
project(ofx-photo VERSION 0.1.0 LANGUAGES CXX)

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
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/ofx-photo.desktop"
        DESTINATION ${CMAKE_INSTALL_DATADIR}/applications)

install(FILES desktop/ofx-photo.svg
        DESTINATION ${CMAKE_INSTALL_DATADIR}/icons/hicolor/scalable/apps)
foreach(size 16 24 32 48 64 128 256)
  install(FILES desktop/icons/${size}/ofx-photo.png
          DESTINATION ${CMAKE_INSTALL_DATADIR}/icons/hicolor/${size}x${size}/apps)
endforeach()

# A prefix outside the XDG defaults needs to be on XDG_DATA_DIRS before the
# menu entry and icon are seen, so say so rather than leave it a mystery.
install(CODE "
  set(share \"${CMAKE_INSTALL_FULL_DATADIR}\")
  if(NOT share MATCHES \"^(/usr/share|/usr/local/share|\$ENV{HOME}/.local/share)\$\")
    message(STATUS \"\")
    message(STATUS \"Installed outside the standard data directories.\")
    message(STATUS \"For the menu entry and icon to be found, add to your shell:\")
    message(STATUS \"    export XDG_DATA_DIRS=\\\"\${share}:\\\$XDG_DATA_DIRS\\\"\")
    message(STATUS \"and make sure ${CMAKE_INSTALL_FULL_BINDIR} is on your PATH.\")
    message(STATUS \"\")
  endif()
")
