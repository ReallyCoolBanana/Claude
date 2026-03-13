#pragma once

#include "types.h"
#include "camera.h"
#include "sphere.h"

#include <vulkan/vulkan.h>
#include <vk_mem_alloc.h>
#include <VkBootstrap.h>
#include <GLFW/glfw3.h>

#include <vector>

struct AllocatedBuffer {
    VkBuffer buffer;
    VmaAllocation allocation;
};

struct AllocatedImage {
    VkImage image;
    VmaAllocation allocation;
    VkImageView view;
};

class VulkanApp {
public:
    void run();

    // GLFW callbacks need access
    Camera camera;
    bool mousePressed = false;
    double lastMouseX = 0.0, lastMouseY = 0.0;
    bool m_framebufferResized = false;

private:
    static constexpr int MAX_FRAMES_IN_FLIGHT = 2;
    static constexpr int WIDTH = 1280;
    static constexpr int HEIGHT = 720;

    GLFWwindow* m_window = nullptr;

    // Vulkan core
    VkInstance m_instance = VK_NULL_HANDLE;
    VkDebugUtilsMessengerEXT m_debugMessenger = VK_NULL_HANDLE;
    VkSurfaceKHR m_surface = VK_NULL_HANDLE;
    VkPhysicalDevice m_physicalDevice = VK_NULL_HANDLE;
    vkb::PhysicalDevice m_vkbPhysicalDevice{};
    VkDevice m_device = VK_NULL_HANDLE;
    VkQueue m_graphicsQueue = VK_NULL_HANDLE;
    VkQueue m_presentQueue = VK_NULL_HANDLE;
    uint32_t m_graphicsQueueFamily = 0;
    VmaAllocator m_allocator = VK_NULL_HANDLE;

    // Swapchain
    VkSwapchainKHR m_swapchain = VK_NULL_HANDLE;
    VkFormat m_swapchainFormat{};
    VkExtent2D m_swapchainExtent{};
    std::vector<VkImage> m_swapchainImages;
    std::vector<VkImageView> m_swapchainImageViews;

    // Depth
    AllocatedImage m_depthImage{};
    VkFormat m_depthFormat = VK_FORMAT_D32_SFLOAT;

    // Render pass & framebuffers
    VkRenderPass m_renderPass = VK_NULL_HANDLE;
    std::vector<VkFramebuffer> m_framebuffers;

    // Pipeline
    VkPipelineLayout m_pipelineLayout = VK_NULL_HANDLE;
    VkPipeline m_graphicsPipeline = VK_NULL_HANDLE;

    // Descriptors
    VkDescriptorSetLayout m_descriptorSetLayout = VK_NULL_HANDLE;
    VkDescriptorPool m_descriptorPool = VK_NULL_HANDLE;
    std::vector<VkDescriptorSet> m_descriptorSets;

    // Per-frame resources
    VkCommandPool m_commandPool = VK_NULL_HANDLE;
    std::vector<VkCommandBuffer> m_commandBuffers;
    std::vector<VkSemaphore> m_imageAvailableSemaphores;
    std::vector<VkSemaphore> m_renderFinishedSemaphores;
    std::vector<VkFence> m_inFlightFences;

    // Uniform buffers
    std::vector<AllocatedBuffer> m_uniformBuffers;
    std::vector<void*> m_uniformBuffersMapped;

    // Geometry
    AllocatedBuffer m_centralSphereVB{};
    AllocatedBuffer m_centralSphereIB{};
    uint32_t m_centralSphereIndexCount = 0;

    AllocatedBuffer m_orbitingSphereVB{};
    AllocatedBuffer m_orbitingSphereIB{};
    uint32_t m_orbitingSphereIndexCount = 0;

    uint32_t m_currentFrame = 0;

    // Init
    void initWindow();
    void initVulkan();
    void mainLoop();
    void cleanup();

    // Vulkan setup
    void createInstance();
    void createSurface();
    void pickPhysicalDevice();
    void createLogicalDevice();
    void createAllocator();
    void createSwapchain();
    void createImageViews();
    void createDepthResources();
    void createRenderPass();
    void createFramebuffers();
    void createDescriptorSetLayout();
    void createGraphicsPipeline();
    void createCommandPool();
    void createCommandBuffers();
    void createSyncObjects();
    void createUniformBuffers();
    void createDescriptorPool();
    void createDescriptorSets();
    void createGeometry();

    // Rendering
    void drawFrame();
    void updateUniformBuffer(uint32_t frameIndex);
    void recordCommandBuffer(VkCommandBuffer cmd, uint32_t imageIndex);

    // Swapchain recreation
    void recreateSwapchain();
    void cleanupSwapchain();

    // Helpers
    AllocatedBuffer createBuffer(VkDeviceSize size, VkBufferUsageFlags usage, VmaMemoryUsage memoryUsage);
    void uploadBuffer(AllocatedBuffer& dst, const void* data, VkDeviceSize size,
                      VkBufferUsageFlags usage);
};
