"""A small arm, as URDF: what the asset upload integration test authors a model from."""

ARM = """<?xml version="1.0"?>
<robot name="arm">
  <material name="steel"><color rgba="0.6 0.6 0.65 1"/></material>
  <link name="base"><visual><geometry><box size="0.2 0.2 0.05"/></geometry><material name="steel"/></visual></link>
  <link name="upper"><visual><origin xyz="0 0 0.15"/><geometry><cylinder radius="0.02" length="0.3"/></geometry><material name="steel"/></visual></link>
  <link name="hand"><visual><geometry><sphere radius="0.03"/></geometry><material name="red"><color rgba="1 0 0 1"/></material></visual></link>
  <link name="finger_l"/>
  <link name="finger_r"/>
  <link name="tool0"/>
  <joint name="shoulder" type="revolute"><parent link="base"/><child link="upper"/><origin xyz="0 0 0.05" rpy="0 0 1.5708"/><axis xyz="0 0 2"/><limit lower="-2" upper="2"/></joint>
  <joint name="wrist" type="continuous"><parent link="upper"/><child link="hand"/><origin xyz="0 0 0.3"/><axis xyz="0 1 0"/></joint>
  <joint name="grip_l" type="prismatic"><parent link="hand"/><child link="finger_l"/><axis xyz="1 0 0"/><limit lower="0" upper="0.04"/></joint>
  <joint name="grip_r" type="prismatic"><parent link="hand"/><child link="finger_r"/><axis xyz="1 0 0"/><limit lower="-0.04" upper="0"/><mimic joint="grip_l" multiplier="-1"/></joint>
  <joint name="flange" type="fixed"><parent link="hand"/><child link="tool0"/><origin xyz="0 0 0.05"/></joint>
</robot>
"""
