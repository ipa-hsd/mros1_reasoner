#!/usr/bin/env python
"""
author: c.h.corbato@tudelft.nl
using Owlready2 to manipulate the ontology

README
- I created a python vrit env for this: $ pew new --python=python3 owlready2
- installed owlready2 in my ws: $ pip install Owlready2
- Make sure you are working in the Virtual environment: $ pew workon owlready2
- run this script by: $ python owl_reasoner.py

"""
import rospy
import rospkg
from owlready2 import *
from rdflib.plugins.sparql import prepareQuery
from rdflib import URIRef

import actionlib

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

from metacontrol_msgs.msg import MvpReconfigurationAction, MvpReconfigurationGoal, \
                                GraphManipulationActionAction,  GraphManipulationActionGoal, \
                                GraphManipulationMessage, SystemState

from collections import defaultdict

import argparse
from decimal import Decimal

from init_models import *

# Initialize global variables
tomasys = None    # owl model with the tomasys ontology
onto = None       # owl model with the application model as individuals of tomasys classes
mock = True       # whether we are running a mock system (True), so reasoning happens in isolation, or connected t the real system
graph = None

# get an instance of RosPack with the default search paths
rospack = rospkg.RosPack()

# Load ontologies: application model and tomasys
def loadOntology(file):
    onto_path.append(rospack.get_path('mc_mdl_tomasys')+'/') # local folder to search for ontologies
    onto_path.append(rospack.get_path('mc_mdl_abb')+'/') # local folder to search for ontologies
    global tomasys, onto
    tomasys = get_ontology("tomasys.owl").load()  # TODO initilize tomasys using the import in the application ontology file
    onto = get_ontology(file).load()


# MVP metacontrol PLAN: returns the FD that is estimated to maximize QA (TODO trade-off) for a given objective o
def obtainBestFunctionDesign(o):
    global tomasys, onto
    f = o.typeF
    # get fds for Function F
    fds = []
    for fd in list(tomasys.FunctionDesign.instances()):
        if fd.solvesF == f:
            fds.append(fd)
    print("== FunctionDesigns available for obj: ", [fd.name for fd in fds])
    print("Objective NFR ENERGY: ", o.hasNFR)
    
    # fiter fds to only those available
    # FILTER if FD realisability is NOT FALSE (TODO check SWRL rules are complete for this)
    realisable_fds = [fd for fd in fds if fd.fd_realisability != False]
    print("== FunctionDesigns REALISABLE for obj: ", [fd.name for fd in realisable_fds])
    # discard FDs already grounded for this objective when objective in error
    suitable_fds= [fd for fd in fds if (not o in fd.fd_error_log)]
    print("== FunctionDesigns suitable NOT IN ERROR LOG: ", [fd.name for fd in suitable_fds])
    # discard those FD that will not meet objective NFRs
    fds_for_obj = meetNFRs(o, suitable_fds)
    print("== FunctionDesigns also meeting NFRs: ", [fd.name for fd in fds_for_obj])

    # get best FD based on higher Utility/trade-off of QAs
    if fds_for_obj != []:
        aux = 0
        best_fd = fds_for_obj[0]
        for fd in fds_for_obj:
            u = utility(fd)
            if  u > aux: 
                best_fd = fd
                aux = u
        
        print("> Best FD available", best_fd.name)
        return best_fd
    else:
        print("*** OPERATOR NEEDED, NO SOLUTION FOUND ***")
        return None

def meetNFRs(o, fds):
    filtered = []
    print("== Checking FDs for Objective with NFRs type: ", o.hasNFR[0].isQAtype.name, "and value: ", o.hasNFR[0].hasValue)
    for fd in fds:
        for nfr in o.hasNFR:
            qas = [qa for qa in fd.hasQAestimation if qa.isQAtype==nfr.isQAtype]               
            if qas == []:
                print("\t WARNING FD has no expected value for this QA")
            else:
                if nfr.isQAtype.name == 'energy':
                    if qas[0].hasValue < nfr.hasValue: # specific semantics for energy
                        filtered.append(fd)
                if nfr.isQAtype.name == 'safety':
                    if qas[0].hasValue > nfr.hasValue:  # specific semantics for energy
                        filtered.append(fd)
    if filtered == []:
        print("#### WARNING: no FDs meetf NFRs")

    return filtered

# MVP: compute expected utility based on QA trade-off, the criteria to chose FDs/configurations
def utility(fd):
    # TODO
    return 1


# Cheops metacontrol PLAN: returns the FD that has the best efficacy for a given objective o
def obtainBestEfficacyFunctionDesign(o):
    global tomasys, onto
    f = o.typeF
    # get fds for Function F
    fds = []
    for fd in list(tomasys.FunctionDesign.instances()):
        if fd.solvesF == f:
            fds.append(fd)
    print("\nFunctionDesigns available for obj ", o.name, ": ", [fd.name for fd in fds])
    aux = 0
    best_fd = None
    for fd in fds:
        # FILTER if FD realisability is NOT FALSE (TODO check SWRL rules are complete for this)
        print("Realisability ", fd.name, fd.fd_realisability)
        if fd.fd_realisability != False:
            # FILTER if this objective has already been attempted by the FD
            # that is the FD error log does NOT contain the current objective
            print(fd.name, "error_log: ", [i.name for i in fd.fd_error_log])
            if not o in fd.fd_error_log:
                if fd.fd_efficacy > aux:
                    best_fd = fd
                    aux = fd.fd_efficacy
    if ( best_fd == None ):
        print("*** OPERATOR NEEDED, NO SOLUTION FOUND ***")
        return None
    else:
        print("\nBest FD available", best_fd.name)
        return best_fd

# MVP: select FD to reconfigure to fix Objective in ERROR
def selectFD(o):
    global tomasys, onto
    print("=> Reasoner searches FD for objective: ", o.name)
    fd = obtainBestFunctionDesign(o)
    if(fd == None):
        print("*** Objective ", o.name, "cannot be realised ***")
        return ["safe_shutdown"]
    else:
        return fd

'''
CHEOPS To solve the given Objective, recursively Grounds the required hierarchy of
sub-Objectives and Function Groundings
'''
def groundObjective(o, cspecs):
    global tomasys, onto
    print("=> Reasoner grounding objective: ", o.name)
    fd = obtainBestFunctionDesign(o)
    if( fd == None ):
        print("*** Objective ", o.name,"cannot be realised ***")
        return ["safe_shutdown"]
    fg = tomasys.FunctionGrounding("fg_" + fd.name)
    print("Roles: ",[r.name for r in fd.roles])
    for r in fd.roles:
        b = tomasys.Binding("b_" + r.name)
        b.binding_role = r
        fg.has_bindings = b # TODO also ad the binding_component
        cspecs.append(r.roleDef)
    for f in fd.requires:
        print("Requires: ", fd.requires)
        ob = onto.search_one(typeF = f) # first search if the objective already exists # TODO check that the existing objective also fulfills the req of the FD
        if ob == None:   # if it does not exist yet, create
            ob = tomasys.Objective("o_" + f.name)
            ob.typeF = f
            fg.needs.append(ob)
            groundObjective(ob, cspecs)
        else:
            fg.needs.append(ob)

    return fg

graph_manipulation_client=None
last_configuration=["cs_yumi_2"]

# Cheops monitoring received
def callbackSystemState(msg):
    sys_state = msg
    global onto

    if sys_state.yumi_status != 1:
        c = onto.search_one(iri="*#c_yumi")
        c.c_status = False
        print("Yumi in ERROR")
    if sys_state.camera_status != 1:
        print("Camera in ERROR")
        c = onto.search_one(iri="*c_camera")
        c.c_status = False
    if sys_state.tag_detection_status != 1:
        print("Tag not detected")
        f = onto.search_one(iri="*f_detect_tag_poses")
        for o in list(tomasys.Objective.instances() ):
            if (o.typeF == f):
                o.o_status = "INTERNAL_ERROR"

    rospy.loginfo(1., 'camera: {}, tag_detect: {}, yumi: {}'.format(
        sys_state.camera_status, sys_state.tag_detection_status, sys_state.yumi_status))

# MVP: callback for diagnostic msg received from QA Observer
def callbackDiagnostics(msg):
    global onto
    for diagnostic_status in msg.status:
        # 2 types of diagnostics considered: about bindings in error (TODO not implemented yet) or about QAs
        if diagnostic_status.message == "binding error":
            updateBinding(diagnostic_status)
        if diagnostic_status.message == "QA status":
            updateQA(diagnostic_status)

def updateBinding(msg):
    # TODO handle reporting of a fg.binding in error
    print("binding error received")

# To reset the inferences that no longer hold due to adaptation
def resetOntologyStatuses():
    for o in list(tomasys.Objective.instances()):
        o.o_status = None

# MVP update QA value based on incoming diagnostic
counter=0
def updateQA(diagnostic_status):
    global onto
    global counter
    counter += 1
    #find the FG that solves the Objective with the same name that the one in the QA message
    fg = onto.search_one(solvesO=onto.search_one(iri="*" + "o_navigateA")) #TODO read objective from diagnostic_status
    if fg == None:
        print("ERROR: FG not found")
        return
    print("received QA about: ", fg)
    if diagnostic_status.values[0].key == "energy":
        fg.hasQAvalue.append( tomasys.QAvalue("obs_energy_{}".format(counter), namespace=onto, isQAtype=onto.search_one(
            iri="*energy"), hasValue=float(diagnostic_status.values[0].value)))
    else:
        print('Unsupported QA type different than _energy_') 

def print_ontology_status():
    global onto
    print("\nComponents Statuses:")
    for i in list(tomasys.ComponentState.instances()):
        print(i.name, i.c_status)

    print("\nBindings Statuses:")
    for i in list(tomasys.Binding.instances()):
        print(i.name, i.b_status)

    print("\nFG Statuses:")
    for i in list(tomasys.FunctionGrounding.instances()):
        print(i.name,"\t", i.fg_status, "\t", [ (qa.name, qa.hasValue) for qa in i.hasQAvalue])

    print("\nObjectives Statuses:")
    for i in list(tomasys.Objective.instances()):
        print(i.name, i.o_status, [(nfr.isQAtype, nfr.hasValue) for nfr in i.hasNFR])

    print("\nCC availability:")
    for i in list(tomasys.ComponentClass.instances()):
        print(i.name, i.cc_availability)

    print("\nFDs information:\n NAME \t")
    for i in list(tomasys.FunctionDesign.instances()):
        print(i.name, "\t", i.fd_realisability, "\t", [
              (qa.isQAtype, qa.hasValue) for qa in i.hasQAestimation])



def timer_cb(event):
    global onto
    rospy.loginfo_throttle(1., 'Entered timer_cb for metacontrol reasoning')
    
    # EXEC REASONING to update ontology with inferences
    # TODO CHECK: update reasoner facts, evaluate, retrieve action, publish
    # update reasoner facts
    try:
        sync_reasoner_pellet(infer_property_values = True, infer_data_property_values = True)
    except owlready2.base.OwlReadyInconsistentOntologyError as err:
        print("Reasoning error: {0}".format(err))
        onto.save(file="error.owl", format="rdfxml")

    # PRINT system status
    print_ontology_status()
    
    # EVALUATE and retrieve desired configuration (MAPE - Analysis)
    # init objectives in error
    objectives_internal_error = []
    for o in list(tomasys.Objective.instances() ):
        if o.o_status == "INTERNAL_ERROR":
            objectives_internal_error.append(o)
    print("\nObjectives in error:", [o.name for o in objectives_internal_error] )

    # ADAPT MAPE -Plan & Execute

    # CHEOPS
    if len(objectives_internal_error) > 1:
        # CHEOPS Ground a solution hierarchy for each root objective in error. We assume here that root_objectives do not share intermediate objectives
        cspecs = []
        for o in objectives_internal_error:
            groundObjective(o, cspecs)

        str_specs = [cs.name for cs in cspecs]
        print("RESULT CONFIG: ", str_specs)
        if len(str_specs) != 0:
            request_reconfiguration()  # CHEOPS request reconfiguration by sending cspecs names
    
    # MVP (TODO fix when CHEOPS also only 1)
    elif len(objectives_internal_error) == 1 :
        fd = selectFD(objectives_internal_error[0])
        # MVP to request new configuration
        if fd != ["safe_shutdown"]:
            result = request_configuration(fd)

            # Adaptation feedback: 
            if result == 1: # reconfiguration executed ok
                print("== RECONFIGURATION SUCCEEDED ==")
                # update the ontology according to the result of the adaptation action - destroy fg for Obj and create the newly grounded one
                fg = onto.search_one(
                    solvesO=objectives_internal_error[0])
                destroy_entity(fg)
                fg = tomasys.FunctionGrounding(
                    "fg_new", namespace=onto, typeFD=fd, solvesO=objectives_internal_error[0])
                resetOntologyStatuses()

            elif result == -1:
                print("== RECONFIGURATION UNKNOWN ==")
            else:
                print("== RECONFIGURATION FAILED ==")

        else:
            print("No FD found to solve Objective, requesting shutdown not available")

    #TODO
    else:
        print("-- NO ADAPTATION NEEDED --")


# Cheops send reconfiguration goal
def send_request (reconfiguration_request):
    global mock
    goal = GraphManipulationActionGoal()
    goal.request = reconfiguration_request
    graph_manipulation_client.send_goal(goal)
    if mock != True:
        graph_manipulation_client.wait_for_result()
        return graph_manipulation_client.get_result().result
    else:
        return GraphManipulationMessage.RECONFIGURATION_OK

# for cheops
spec2request = defaultdict(lambda: GraphManipulationMessage.REQUEST_MISSING, {
    "cs_yumi1" : GraphManipulationMessage.REQUEST_YUMI_CONFIG_1ARM,
    "cs_displacement_node" : GraphManipulationMessage.REQUEST_DISPLACEMENT_NODE,
    "cs_tag_calibration_node" : GraphManipulationMessage.REQUEST_TAG_CALIBRATION_NODE,
    "cs_camera_1" : GraphManipulationMessage.REQUEST_CAMERA_CONFIG1,
    "cs_camera_2" : GraphManipulationMessage.REQUEST_CAMERA_CONFIG2,
    "cs_tag_detector" : GraphManipulationMessage.REQUEST_TAG_DETECTOR,
    "safe_shutdown" : GraphManipulationMessage.REQUEST_SAFE_SHUTDOWN
})

# cheops reconfiguration
def request_reconfiguration(component_specs):
    rospy.logwarn_throttle(1., 'Reconfiguration requested ..')
    global last_configuration, spec2request
    new_specs = filter(lambda cs: cs not in last_configuration, component_specs)
    requests = map(lambda cs: spec2request[cs], new_specs)

    for r in requests:
        rospy.logwarn_throttle(1., 'Requesting reconfig: {}'.format(r))
        result = send_request(r)
        if (result != GraphManipulationMessage.RECONFIGURATION_OK):
            last_configuration = []
            return result

    last_configuration = component_specs
    return GraphManipulationMessage.RECONFIGURATION_OK

# for MVP with QAs - request the FD.name to reconfigure to
def request_configuration(fd):
    rospy.logwarn_throttle(1., 'New Configuration requested: {}'.format(fd.name))

    goal = MvpReconfigurationGoal()
    goal.desired_configuration_name = fd.name
    rosgraph_manipulator_client.send_goal(goal)
    rosgraph_manipulator_client.wait_for_result()
    result = rosgraph_manipulator_client.get_result().result
    print('Result: ', result)
    return result


if __name__ == '__main__':
    # load ontology
    onto_file = rospy.get_param('/onto_file')
    loadOntology(onto_file)
    rospy.loginfo("Loaded ontology: " + onto_file)
    # init specific application model using the corresponding init sript
    if onto_file == "abb_scenario2.owl" :
        init_abb_2a(onto, tomasys)
    elif onto_file == "abb_dualarm_mm_complete.owl":
        init_abb_2b(onto, tomasys)
    elif onto_file == "mvp.owl":
        init_mvp(onto, tomasys)
    elif onto_file == "abb_scenario3.owl":
        init_abb_3(onto, tomasys)
    else:
        print("Unknown ontology file: ", onto_file)

    print(onto_file)

    graph = default_world.as_rdflib_graph()
    graph.get_context(onto)

    tomasys = URIRef('http://metacontrol.org/tomasys#')
    abb_scn2 = URIRef('http://abb_scenario2#')
    q = prepareQuery('''SELECT ?p
                       WHERE {?p ?o tomasys:Function .}''', initNs={'abb_scn2':abb_scn2, 'tomasys':tomasys})
    for row in graph.query(q):
        print(row)