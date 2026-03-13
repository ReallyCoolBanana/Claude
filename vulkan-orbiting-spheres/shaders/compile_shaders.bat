@echo off
echo Compiling shaders...
%VULKAN_SDK%\Bin\glslc.exe shader.vert -o shader.vert.spv
%VULKAN_SDK%\Bin\glslc.exe shader.frag -o shader.frag.spv
echo Done.
pause
