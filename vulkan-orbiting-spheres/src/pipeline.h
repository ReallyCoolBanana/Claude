#pragma once

#include <vulkan/vulkan.h>
#include <string>
#include <vector>

std::vector<char> readShaderFile(const std::string& filepath);

VkShaderModule createShaderModule(VkDevice device, const std::vector<char>& code);

struct PipelineConfig {
    VkDevice device;
    VkRenderPass renderPass;
    VkDescriptorSetLayout descriptorSetLayout;
    VkExtent2D extent;
};

struct PipelineResult {
    VkPipeline pipeline;
    VkPipelineLayout pipelineLayout;
};

PipelineResult createGraphicsPipeline(const PipelineConfig& config);
