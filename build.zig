const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{ .preferred_optimize_mode = .ReleaseSafe });

    const core_mod = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
    });

    const lib = b.addLibrary(.{
        .linkage = .dynamic,
        .name = "bolorgir",
        .root_module = core_mod,
        .version = .{ .major = 0, .minor = 1, .patch = 0 },
        // The self-hosted x86_64 backend miscompiles this code in Debug
        // (values kept in caller-saved registers across calls, e.g. a live
        // pointer in %r11/%rdx clobbered by callees such as __tls_get_addr).
        .use_llvm = true,
    });
    b.installArtifact(lib);

    const test_mod = b.createModule(.{
        .root_source_file = b.path("src/tests.zig"),
        .target = target,
        .optimize = optimize,
    });
    const unit_tests = b.addTest(.{ .root_module = test_mod, .use_llvm = true });
    const run_tests = b.addRunArtifact(unit_tests);
    const test_step = b.step("test", "Run core unit tests");
    test_step.dependOn(&run_tests.step);

    const exe_mod = b.createModule(.{
        .root_source_file = null,
        .target = target,
        .optimize = optimize,
    });
    const c_example = b.addExecutable(.{ .name = "c_client", .root_module = exe_mod });
    c_example.addCSourceFile(.{ .file = b.path("examples/c_client.c"), .flags = &.{"-std=c99"} });
    c_example.addIncludePath(b.path("include"));
    c_example.linkLibrary(lib);
    c_example.linkLibC();
    const example_step = b.step("example-c", "Build the C client example");
    example_step.dependOn(&b.addInstallArtifact(c_example, .{}).step);

    const fuzz_mod = b.createModule(.{
        .root_source_file = b.path("src/fuzz_main.zig"),
        .target = target,
        .optimize = optimize,
    });
    const fuzz_exe = b.addExecutable(.{ .name = "blg_fuzz", .root_module = fuzz_mod, .use_llvm = true });
    const install_fuzz = b.addInstallArtifact(fuzz_exe, .{});
    const fuzz_build_step = b.step("fuzz-build", "Build the T4 fuzz runner");
    fuzz_build_step.dependOn(&install_fuzz.step);
    const run_fuzz = b.addRunArtifact(fuzz_exe);
    if (b.args) |args| run_fuzz.addArgs(args);
    const fuzz_step = b.step("fuzz", "Run the T4 fuzz campaigns");
    fuzz_step.dependOn(&run_fuzz.step);
}
