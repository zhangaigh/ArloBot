#!/usr/bin/env python
# Software License Agreement (BSD License)
#
# Author: Chris L8 https://github.com/chrisl8
# URL: https://github.com/chrisl8/ArloBot
#
#Derived from \opt\ros\hydro\lib\create_node\turtlebot_node.py
#This is based on turtlebot_node adapted to run on a Propeller Activity Board based ArloBot
#
#When upgrading to new versions of ROS,
#or when attempting to integrate new TurtleBot functions,
#please look at and compare turtlebot_node.py to the new version
#to see what you may need to add/improve/replace
#to make things work.
#
#Special thanks to arduino.py by Dr. Rainer Hessmer
#https://code.google.com/p/drh-robotics-ros/
#Much of my code below is based on or copied from his work.
#
# NOTE: This script REQUIRES parameters to be loaded from param/encoders.yaml!
#import roslib; roslib.load_manifest('arlobot') # http://wiki.ros.org/roslib

import rospy
import tf
from math import sin, cos
import sys

from geometry_msgs.msg import Quaternion
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from std_msgs.msg import String
from std_msgs.msg import Bool

#For USB relay board
from pylibftdi import BitBangDevice

from SerialDataGateway import SerialDataGateway

class PropellerComm(object):
    '''
    Helper class for communicating with a Propeller board over serial port
    '''

    def __init__(self, port="/dev/ttyUSB0", baudrate=115200):

        self._Counter = 0 # For Propeller code's _HandleReceivedLine and _WriteSerial
        self._motorsOn = 0 # Set to 1 if the motors are on, used with USB Relay Control board
        self._SafeToOperate = 0 # Use arlobot_safety to set this

        rospy.init_node('arlobot')

        # Subscriptions
        rospy.Subscriber("cmd_vel", Twist, self._HandleVelocityCommand) # Is this line or the below bad redundancy?
        rospy.Subscriber("cmd_vel_mux/input/teleop", Twist, self._HandleVelocityCommand) # IS this line or the above bad redundancy?
        rospy.Subscriber("arlobot_safety/safeToGo", Bool, self._SafetyShutdown) # Safty Shutdown

        # Publishers
        self._SerialPublisher = rospy.Publisher('serial', String, queue_size=10)

        # IF the Odometry Transform is done with the robot_pose_ekf do not publish it,
        # but we are not using robot_pose_ekf, because it does nothing for us if you don't have a full IMU!
        self._OdometryTransformBroadcaster = tf.TransformBroadcaster() # REMOVE this line if you use robot_pose_ekf
        self._OdometryPublisher = rospy.Publisher("odom", Odometry, queue_size=10)

        # We don't need to broadcast a transform, as it is static and contained within the URDF files
        #self._SonarTransformBroadcaster = tf.TransformBroadcaster()
        self._UltraSonicPublisher = rospy.Publisher("ultrasonic_scan", LaserScan, queue_size=10)
        self._InfraredPublisher = rospy.Publisher("infrared_scan", LaserScan, queue_size=10)
        
        port = rospy.get_param("~port", "/dev/ttyUSB0")
        baudRate = int(rospy.get_param("~baudRate", 115200))

        rospy.loginfo("Starting with serial port: " + port + ", baud rate: " + str(baudRate))
        self._SerialDataGateway = SerialDataGateway(port, baudRate,  self._HandleReceivedLine)

    def _HandleReceivedLine(self,  line): # This is Propeller specific
        self._Counter = self._Counter + 1
        #rospy.logdebug(str(self._Counter) + " " + line)
        #if (self._Counter % 50 == 0):
        self._SerialPublisher.publish(String(str(self._Counter) + ", in:  " + line))

        if (len(line) > 0):
            lineParts = line.split('\t')
            if (lineParts[0] == 'o'):
                if self._motorsOn == 1:
                    self._BroadcastOdometryInfo(lineParts)
                    return
                elif self._SafeToOperate == 1: # Only turn on motors if it is safe to operate
                    self._SwitchMotors("on")
                    return
            if (lineParts[0] == 'i'):
                self._InitializeDriveGeometry(lineParts)
                return
            if (lineParts[0] == 's'): # Arlo Status info, such as sensors.
                rospy.loginfo("Propeller: " + line)
                return
                
    def _SafetyShutdown(self, safe):
        if safe.data:
            self._SafeToOperate = 1
        else:
            self._SafeToOperate = 0
            if self._motorsOn == 1:
                self._SwitchMotors("off")
            rospy.logdebug("Stopping")

    def _BroadcastOdometryInfo(self, lineParts):
        # This broadcasts ALL info from the Propeller based robot every time data comes in
        partsCount = len(lineParts)

        #rospy.logwarn(partsCount)
        if (partsCount  != 8): # Just discard short/long lines, increment this as lines get longer
            pass
        
        try:
            x = float(lineParts[1])
            y = float(lineParts[2])
            # 3 is odom based heading and 4 is gyro based
            # If there is some way to "integrate" these, go for it!
            theta = float(lineParts[3]) # Using odom on Arlo for now to see if it works OK
            
            vx = float(lineParts[5])
            omega = float(lineParts[6])
        
            #quaternion = tf.transformations.quaternion_from_euler(0, 0, theta)
            quaternion = Quaternion()
            quaternion.x = 0.0 
            quaternion.y = 0.0
            quaternion.z = sin(theta / 2.0)
            quaternion.w = cos(theta / 2.0)
            
            
            rosNow = rospy.Time.now()
            
            # First, we'll publish the transform from frame odom to frame base_link over tf
            # Note that sendTransform requires that 'to' is passed in before 'from' while
            # the TransformListener' lookupTransform function expects 'from' first followed by 'to'.
            # This transform conflicts with transforms built into the Turtle stack
            # http://wiki.ros.org/tf/Tutorials/Writing%20a%20tf%20broadcaster%20%28Python%29
            # This is done in/with the robot_pose_ekf because it can integrate IMU/gyro data
            # using an "extended Kalman filter"
            # REMOVE this "line" if you use robot_pose_ekf
            self._OdometryTransformBroadcaster.sendTransform(
                (x, y, 0), 
                (quaternion.x, quaternion.y, quaternion.z, quaternion.w),
                rosNow,
                "base_footprint",
                "odom"
                )

            # next, we will publish the odometry message over ROS
            odometry = Odometry()
            odometry.header.frame_id = "odom"
            odometry.header.stamp = rosNow
            odometry.pose.pose.position.x = x
            odometry.pose.pose.position.y = y
            odometry.pose.pose.position.z = 0
            odometry.pose.pose.orientation = quaternion

            odometry.child_frame_id = "base_link"
            odometry.twist.twist.linear.x = vx
            odometry.twist.twist.linear.y = 0
            odometry.twist.twist.angular.z = omega

            # robot_pose_ekf needs these covariances and we may need to adjust them.
            # From: ~/turtlebot/src/turtlebot_create/create_node/src/create_node/covariances.py
            # However, this is not needed because we are not using robot_pose_ekf
            '''
            odometry.pose.covariance = [1e-3, 0, 0, 0, 0, 0,
                                    0, 1e-3, 0, 0, 0, 0,
                                    0, 0, 1e6, 0, 0, 0,
                                    0, 0, 0, 1e6, 0, 0,
                                    0, 0, 0, 0, 1e6, 0,
                                    0, 0, 0, 0, 0, 1e3]

            odometry.twist.covariance = [1e-3, 0, 0, 0, 0, 0,
                                     0, 1e-3, 0, 0, 0, 0,
                                     0, 0, 1e6, 0, 0, 0,
                                     0, 0, 0, 1e6, 0, 0,
                                     0, 0, 0, 0, 1e6, 0,
                                     0, 0, 0, 0, 0, 1e3]
                                     '''

            self._OdometryPublisher.publish(odometry)

            # Joint State for Turtlebot stack
            # Note without this transform publisher the wheels will
            # be white, stuck at 0, 0, 0 and RVIZ will tell you that
            # there is no transform from the wheel_links to the base_
            '''
            # Instead of publishing a stream of pointless transforms,
            # How about if I just make the joint static in the URDF?
            # create.urdf.xacro:
            # <joint name="right_wheel_joint" type="fixed">
            # NOTE This may prevent Gazebo from working with this model
            js = JointState(name = ["left_wheel_joint", "right_wheel_joint", "front_castor_joint", "back_castor_joint"],
                            position=[0,0,0,0], velocity=[0,0,0,0], effort=[0,0,0,0])
            js.header.stamp = rosNow
            self.joint_states_pub.publish(js)
            '''

            # Fake laser from "PING" Ultrasonic Sensor and IR Distance Sensor input:
            # http://wiki.ros.org/navigation/Tutorials/RobotSetup/TF
            '''
            The purpose of this is two fold:
            1. It REALLY helps adjusting values in the Propeller and ROS when I can visualize the sensor output in RVIZ!
                For this purpose, a lot of the parameters are a matter of personal taste. Whatever makes it easiest to visualize is best.
            2. I want to allow AMCL to use this data to avoid obstacles that the Kinect/Xtion miss.
                For the second purpose, some of the parameters here may need to be tweaked, to adjust how large an object looks to AMCL.
            Note that we should also adjust the distance at which AMCL takes this data into account either here or elsewhere.
            '''
            # Transform: http://wiki.ros.org/tf/Tutorials/Writing%20a%20tf%20broadcaster%20%28Python%29
            '''
            We do not need to broadcast a transform,
            because it is static and contained within the URDF files now.
            self._SonarTransformBroadcaster.sendTransform(
                (0.1, 0.0, 0.2), 
                (0, 0, 0, 1),
                rosNow,
                "sonar_laser",
                "base_link"
                )
                '''
            # Some help: http://books.google.com/books?id=2ZL9AAAAQBAJ&pg=PT396&lpg=PT396&dq=fake+LaserScan+message&source=bl&ots=VJMfSYXApG&sig=s2YgiHTA3i1OjVyPxp2aAslkW_Y&hl=en&sa=X&ei=B_vDU-LkIoef8AHsooHICA&ved=0CG0Q6AEwCQ#v=onepage&q=fake%20LaserScan%20message&f=false
            # Question: I'm doing this all in degrees and then converting to Radians later. Is there any way to do this in Radians? I just don't know how to create and fill an array with "Radians" since they are not rational numbers, but multiples of PI. Thus the degrees
            num_readings = 360 # How about 1 per degree?
            laser_frequency = 100 # I'm not sure how to decide what to use here.
            #ranges = [1] * num_readings # Fill array with fake "1" readings for testing
            PINGranges = [0] * num_readings # Fill array with 0 and then overlap with real readings
            IRranges = [0] * num_readings # Fill array with 0 and then overlap with real readings
            
            # Note that sensor orientation is important here! If you have a different number or aim them differently this will not work!
            # TODO: Tweak this value based on real measurements! Use both IR and PING sensors.
            sensorOffset = 0.22545 # The offset between the pretend sensor location in the URDF and real location needs to be added to these values. This may need to be tweaked.
            pingRange0 = (int(lineParts[7]) / 100.0) + sensorOffset # Convert cm to meters and add offset
            irRange0 = (int(lineParts[8]) / 100.0) + sensorOffset # Convert cm to meters and add offset
            pingRange1 = (int(lineParts[9]) / 100.0) + sensorOffset
            irRange1 = (int(lineParts[10]) / 100.0) + sensorOffset
            pingRange2 = (int(lineParts[11]) / 100.0) + sensorOffset # Center forward sensor.
            irRange2 = (int(lineParts[12]) / 100.0) + sensorOffset # Center forward sensor.
            pingRange3 = (int(lineParts[13]) / 100.0) + sensorOffset
            irRange3 = (int(lineParts[14]) / 100.0) + sensorOffset
            pingRange4 = (int(lineParts[15]) / 100.0) + sensorOffset
            irRange4 = (int(lineParts[16]) / 100.0) + sensorOffset
            pingRange5 = (int(lineParts[17]) / 100.0) + sensorOffset # Rear sensor, note these numbers can change if you add more sensors!
            irRange5 = (int(lineParts[18]) / 100.0) + sensorOffset # Rear sensor, note these numbers can change if you add more sensors!
            # I'm going to start by just kind of "filling in" the area with the data and then adjust based on experimentation.
            '''
            The sensors are 11cm from center to center at the front of the base plate.
            The radius of the base plate is 22.545 cm
            = 28 degree difference (http://ostermiller.org/calc/triangle.html)
            '''
            sensorSeperation = 28
            sensorSpread = 10 # This is how wide of an arc (in degrees) to paint for each "hit"
            '''
            NOTE:
            This assumes that things get bigger as they are further away. This is true of the PING's area,
            and while it may or may not be true of the object the PING sees, we have no way of knowing if
            the object fills the ping's entire field of view or only a small part of it, a "hit" is a "hit".
            However for the IR sensor, the objects are points, that are the same size regardless of distance,
            so we are clearly inflating them here.
            '''
            
            for x in range(180 - sensorSpread / 2, 180 + sensorSpread / 2):
                PINGranges[x] = pingRange5 # Rear Sensor
                IRranges[x] = irRange5 # Rear Sensor

            for x in range((360 - sensorSeperation * 2) - sensorSpread / 2, (360 - sensorSeperation * 2) + sensorSpread / 2):
                PINGranges[x] = pingRange4
                IRranges[x] = irRange4

            for x in range((360 - sensorSeperation) - sensorSpread / 2, (360 - sensorSeperation) + sensorSpread / 2):
                PINGranges[x] = pingRange3
                IRranges[x] = irRange3

            for x in range(360 - sensorSpread / 2, 360):
                PINGranges[x] = pingRange2
                IRranges[x] = irRange2
            # Crosses center line
            for x in range(0, sensorSpread /2):
                PINGranges[x] = pingRange2
                IRranges[x] = irRange2
            
            for x in range(sensorSeperation - sensorSpread / 2, sensorSeperation + sensorSpread / 2):
                PINGranges[x] = pingRange1
                IRranges[x] = irRange1
            
            for x in range((sensorSeperation * 2) - sensorSpread / 2, (sensorSeperation * 2) + sensorSpread / 2):
                PINGranges[x] = pingRange0
                IRranges[x] = irRange0

            # LaserScan: http://docs.ros.org/api/sensor_msgs/html/msg/LaserScan.html
            ultrasonic_scan = LaserScan()
            infrared_scan = LaserScan()
            ultrasonic_scan.header.stamp = rosNow
            infrared_scan.header.stamp = rosNow
            ultrasonic_scan.header.frame_id = "ping_sensor_array"
            infrared_scan.header.frame_id = "ir_sensor_array"
            # For example:
            #scan.angle_min = -45 * M_PI / 180; // -45 degree
            #scan.angle_max = 45 * M_PI / 180;   // 45 degree
            # if you want to receive a full 360 degrees scan, you should try setting min_angle to -pi/2 and max_angle to 3/2 * pi.
            # Radians: http://en.wikipedia.org/wiki/Radian#Advantages_of_measuring_in_radians
            ultrasonic_scan.angle_min = 0
            infrared_scan.angle_min = 0
            ultrasonic_scan.angle_max = 2 * 3.14159 # Full circle
            infrared_scan.angle_max = 2 * 3.14159 # Full circle
            ultrasonic_scan.scan_time = 1 # I think this is only really applied for 3D scanning
            infrared_scan.scan_time = 1 # I think this is only really applied for 3D scanning
            # Make sure the part you divide by num_readings is the same as your angle_max!
            # Might even make sense to use a variable here?
            ultrasonic_scan.angle_increment = (2 * 3.14) / num_readings
            infrared_scan.angle_increment = (2 * 3.14) / num_readings
            ultrasonic_scan.time_increment = (1 / laser_frequency) / (num_readings)
            infrared_scan.time_increment = (1 / laser_frequency) / (num_readings)
            # From: http://www.parallax.com/product/28015
            # Range: approximately 1 inch to 10 feet (2 cm to 3 m)
            # This should be adjusted based on the imaginary distance between the actual laser
            # and the laser location in the URDF file.
            ultrasonic_scan.range_min = 0.02 # in Meters Distances below this number will be ignored REMEMBER the offset!
            infrared_scan.range_min = 0.02 # in Meters Distances below this number will be ignored REMEMBER the offset!
            ultrasonic_scan.range_max = 3 # in Meters Distances above this will be ignored
            infrared_scan.range_max = 3 # in Meters Distances above this will be ignored
            ultrasonic_scan.ranges = PINGranges
            infrared_scan.ranges = IRranges
            # "intensity" is a value specific to each laser scanner model.
            # It can safely be ignored
            
            self._UltraSonicPublisher.publish(ultrasonic_scan)
            self._InfraredPublisher.publish(infrared_scan)

        except:
            rospy.logwarn("Unexpected error:" + str(sys.exc_info()[0]))

    def _WriteSerial(self, message):
        self._SerialPublisher.publish(String(str(self._Counter) + ", out: " + message))
        self._SerialDataGateway.Write(message)

    def Start(self):
        rospy.logdebug("Starting")
        self._SerialDataGateway.Start()
        # Do not put anything here, it won't get run until the SerialDataGateway is stopped.

    def Stop(self):
        rospy.logdebug("Stopping")
        self._SwitchMotors("off")
        #self._SerialDataGateway.Break() # Reset the propeller board # Actually the board resets when you close the serial port!
        self._SerialDataGateway.Stop()
        
    def _HandleVelocityCommand(self, twistCommand): # This is Propeller specific
        # NOTE: turtlebot_node has a lot of code under its cmd_vel function to deal with maximum and minimum speeds,
        # which are dealt with in ArloBot on the Activity Board itself in the Propeller code.
        """ Handle movement requests. """
        if self._SafeToOperate: # Do not move if it is not self._SafeToOperate
            v = twistCommand.linear.x        # m/s
            omega = twistCommand.angular.z      # rad/s
            #rospy.logdebug("Handling twist command: " + str(v) + "," + str(omega))
            message = 's,%.3f,%.3f\r' % (v, omega)
            #rospy.logdebug("Sending speed command message: " + message)
            self._WriteSerial(message)
        else:
            message= 's,0.0,0.0\r' # Tell it to be still if it is not SafeToOperate
            self._WriteSerial(message)

    def _InitializeDriveGeometry(self, lineParts):
        if self._SafeToOperate:
            trackWidth = rospy.get_param("~driveGeometry/trackWidth", "0")
            distancePerCount = rospy.get_param("~driveGeometry/distancePerCount", "0")
            message = 'd,%f,%f\r' % (trackWidth, distancePerCount)
            #rospy.logdebug("Sending drive geometry params message: " + message)
            self._WriteSerial(message)
        else:
            rospy.loginfo(lineParts[1])
        
    def _SwitchMotors(self, state):
        relayExists = rospy.get_param("~usbRelayInstalled", False)
        if relayExists:
            # Start Motors
            # For SainSmart 8 port USB model http://www.sainsmart.com/sainsmart-4-channel-12-v-usb-relay-board-module-controller-for-automation-robotics-1.html
            # Note that this is specific to this model, if you want me to code for various models let me know and I can work with you to expand the code
            # to cover more models and add ROS parameters for picking your model.
            class relay(dict):
                address = {
                    "1":"1",
                    "2":"2",
                    "3":"4",
                    "4":"8",
                    "5":"10",
                    "6":"20",
                    "7":"40",
                    "8":"80",
                    "all":"FF"
                    }
            relaySerialNumber = rospy.get_param("~usbRelaySerialNumber", "")
            leftMotorRelay = rospy.get_param("~usbLeftMotorRelay", "")
            rightMotorRelay = rospy.get_param("~usbRightMotorRelay", "")
            if state == "on":
                BitBangDevice(relaySerialNumber).port |= int(relay.address[leftMotorRelay], 16)
                BitBangDevice(relaySerialNumber).port |= int(relay.address[rightMotorRelay], 16)
                self._motorsOn = 1
            elif state == "off":
                BitBangDevice(relaySerialNumber).port &= ~int(relay.address[leftMotorRelay], 16)
                BitBangDevice(relaySerialNumber).port &= ~int(relay.address[rightMotorRelay], 16)
                self._motorsOn = 0
        else: # If no automated motor control exists, just set the state blindly.
            if state == "on":
                self._motorsOn = 1
            elif state == "off":
                self._motorsOn = 0

if __name__ == '__main__':
    propellercomm = PropellerComm()
    rospy.on_shutdown(propellercomm.Stop)
    try:
        propellercomm.Start()
        rospy.spin()

    except rospy.ROSInterruptException:
        propellercomm.Stop()
    

